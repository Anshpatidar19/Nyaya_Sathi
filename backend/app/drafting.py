"""Drafting + red-lining agent.

Two capabilities, one grounding rule:

  draft()   plain-English instructions -> a complete legal document
  review()  counterparty's document    -> flagged clauses with explanations

THE GROUNDING RULE: every legal citation attached to a clause or a red-line
flag must point at a statute section that was actually retrieved from the
local bare-act index. Gemini is never allowed to cite from memory. A concern
it can't ground becomes a "drafting" flag with no citation rather than an
invented section number - fabricated citations in legal documents are the
single most damaging thing this feature could do.

Document types live in DOC_TYPES. Each entry declares the fields a draft
needs and the statute queries used to pull governing law into context.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from . import gemini, statutes

logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 24000     # counterparty documents are truncated to this
STATUTES_PER_QUERY = 2


# ---------------------------------------------------------------------------
# Document catalogue
# ---------------------------------------------------------------------------
# needs_advocate=True  -> anything filed in court. Drafted, but the response
#                         carries a review warning and the UI must show it.

DOC_TYPES: Dict[str, Dict[str, Any]] = {
    "cheque_bounce_notice": {
        "name": "Cheque bounce demand notice",
        "category": "Notice",
        "needs_advocate": False,
        "fields": ["sender", "recipient", "cheque_number", "cheque_date",
                   "amount", "bank", "return_date", "return_reason"],
        "statute_queries": ["dishonour of cheque insufficient funds",
                            "notice demand payment cheque"],
        "guidance": (
            "Governed by Section 138 of the Negotiable Instruments Act. The "
            "notice must be sent within 30 days of the bank's return memo, "
            "and must demand payment within 15 days of receipt. State both "
            "deadlines explicitly."
        ),
    },
    "legal_notice": {
        "name": "General legal notice",
        "category": "Notice",
        "needs_advocate": False,
        "fields": ["sender", "recipient", "subject", "facts", "demand", "deadline"],
        "statute_queries": ["breach of contract remedy", "criminal breach of trust"],
        "guidance": (
            "State facts chronologically with dates, then the specific demand, "
            "then a compliance deadline, then the consequence of non-compliance."
        ),
    },
    "eviction_notice": {
        "name": "Notice to tenant / quit notice",
        "category": "Notice",
        "needs_advocate": False,
        "fields": ["landlord", "tenant", "property_address", "reason",
                   "notice_period", "rent_due"],
        "statute_queries": ["tenancy notice to quit", "lease determination"],
        "guidance": (
            "Section 106 of the Transfer of Property Act sets the default "
            "notice period where the agreement is silent - 15 days for a "
            "month-to-month tenancy. State rent control law may require more. "
            "Say clearly that possession is sought through due process only."
        ),
    },
    "reply_notice": {
        "name": "Reply to a legal notice",
        "category": "Notice",
        "needs_advocate": False,
        "fields": ["sender", "recipient", "original_notice_date", "response_points"],
        "statute_queries": ["defamation reply", "breach of contract defence"],
        "guidance": (
            "Deny each allegation paragraph by paragraph, referring to the "
            "original notice's numbering. Do not admit anything by silence."
        ),
    },
    "nda": {
        "name": "Non-disclosure agreement",
        "category": "Agreement",
        "needs_advocate": False,
        "fields": ["party_a", "party_b", "purpose", "duration", "mutual",
                   "governing_state"],
        "statute_queries": ["agreement in restraint of trade void",
                            "lawful consideration contract"],
        "guidance": (
            "Define confidential information, list carve-outs (already public, "
            "independently developed, required by law), set a survival period, "
            "and name the courts with jurisdiction. Note that Section 27 of the "
            "Indian Contract Act voids agreements in restraint of trade, so "
            "non-compete style clauses are largely unenforceable in India."
        ),
    },
    "rent_agreement": {
        "name": "Rent / lease agreement",
        "category": "Agreement",
        "needs_advocate": False,
        "fields": ["landlord", "tenant", "property_address", "rent", "deposit",
                   "duration", "start_date", "governing_state"],
        "statute_queries": ["lease of immoveable property", "tenancy rights"],
        "guidance": (
            "Cover rent, escalation, deposit and its refund timeline, "
            "maintenance split, lock-in, notice period, and permitted use. "
            "Leases of a year or more require registration."
        ),
    },
    "employment_contract": {
        "name": "Employment agreement / offer letter",
        "category": "Agreement",
        "needs_advocate": False,
        "fields": ["employer", "employee", "role", "salary", "start_date",
                   "notice_period", "location"],
        "statute_queries": ["restraint of trade employment",
                            "termination notice workman"],
        "guidance": (
            "Post-employment non-competes are void under Section 27 of the "
            "Contract Act; confidentiality and non-solicitation survive better. "
            "Notice period must be reciprocal to be defensible."
        ),
    },
    "service_agreement": {
        "name": "Service / consultancy agreement",
        "category": "Agreement",
        "needs_advocate": False,
        "fields": ["client", "provider", "scope", "fee", "payment_terms",
                   "duration", "governing_state"],
        "statute_queries": ["contract performance breach", "lawful consideration"],
        "guidance": (
            "Define deliverables and acceptance criteria precisely - vague "
            "scope is the commonest source of dispute. Cover IP ownership, "
            "payment timelines, and termination for convenience."
        ),
    },
    "loan_agreement": {
        "name": "Loan agreement / promissory note",
        "category": "Agreement",
        "needs_advocate": False,
        "fields": ["lender", "borrower", "amount", "interest_rate",
                   "repayment_schedule", "security"],
        "statute_queries": ["promissory note negotiable instrument",
                            "cheating dishonestly inducing delivery"],
        "guidance": (
            "State principal, interest rate, repayment schedule, and default "
            "consequences. Interest must not be unconscionable."
        ),
    },
    "consumer_complaint": {
        "name": "Consumer complaint",
        "category": "Complaint",
        "needs_advocate": False,
        "fields": ["complainant", "opposite_party", "purchase_date", "amount",
                   "defect", "relief_sought"],
        "statute_queries": ["defective goods deficiency service",
                            "cheating consumer"],
        "guidance": (
            "Filed under the Consumer Protection Act, 2019, online via the "
            "e-Daakhil portal. State the transaction, the defect or deficiency, "
            "the attempts made to resolve it, and the relief claimed."
        ),
    },
    "police_complaint": {
        "name": "Police complaint / FIR request",
        "category": "Complaint",
        "needs_advocate": False,
        "fields": ["complainant", "incident_date", "incident_place",
                   "accused", "facts"],
        "statute_queries": ["theft", "cheating", "criminal intimidation",
                            "hurt assault"],
        "guidance": (
            "Address it to the Station House Officer. Facts in chronological "
            "order with dates, times and places. Name the applicable BNS "
            "sections only where the retrieved provisions clearly fit. Ask for "
            "a copy of the FIR and its number."
        ),
    },
    "rti_application": {
        "name": "RTI application",
        "category": "Application",
        "needs_advocate": False,
        "fields": ["applicant", "public_authority", "information_sought", "period"],
        "statute_queries": ["right to information public authority"],
        "guidance": (
            "Under the Right to Information Act, 2005. Address the Public "
            "Information Officer. Ask specific, answerable questions - one per "
            "numbered point. Fee is Rs 10 for central authorities."
        ),
    },
    "affidavit": {
        "name": "Affidavit",
        "category": "Court document",
        "needs_advocate": True,
        "fields": ["deponent", "address", "purpose", "statements"],
        "statute_queries": ["false evidence affidavit", "punishment false statement"],
        "guidance": (
            "First person, numbered paragraphs, ending with a verification "
            "clause stating what is true to personal knowledge and what is "
            "believed on advice. Must be sworn before a notary or oath "
            "commissioner. A false affidavit is itself an offence."
        ),
    },
    "bail_application": {
        "name": "Bail application",
        "category": "Court document",
        "needs_advocate": True,
        "fields": ["applicant", "case_number", "court", "sections_charged",
                   "arrest_date", "grounds"],
        "statute_queries": ["bail bond release", "arrest detention rights"],
        "guidance": (
            "Cause title, then the facts of arrest, then the grounds for bail "
            "(no flight risk, no tampering, roots in the community, period "
            "already in custody), then the prayer."
        ),
    },
    "writ_petition": {
        "name": "Writ petition",
        "category": "Court document",
        "needs_advocate": True,
        "fields": ["petitioner", "respondent", "court", "facts",
                   "rights_violated", "relief_sought"],
        "statute_queries": ["remedies enforcement fundamental rights writs",
                            "equality before law", "life personal liberty"],
        "guidance": (
            "Article 32 for the Supreme Court, Article 226 for a High Court. "
            "Cause title, jurisdiction, facts, grounds tied to specific "
            "fundamental rights, then the prayer. Must be settled by an "
            "advocate before filing."
        ),
    },
    "power_of_attorney": {
        "name": "Power of attorney",
        "category": "Deed",
        "needs_advocate": False,
        "fields": ["grantor", "attorney", "powers", "duration", "property"],
        "statute_queries": ["agency authority agent principal"],
        "guidance": (
            "List the granted powers exhaustively - anything not listed is not "
            "granted. A POA touching immoveable property needs registration. "
            "State clearly whether it is revocable."
        ),
    },
    "partnership_deed": {
        "name": "Partnership deed",
        "category": "Deed",
        "needs_advocate": False,
        "fields": ["partners", "firm_name", "business", "capital",
                   "profit_sharing", "duration"],
        "statute_queries": ["partnership firm rights duties partners"],
        "guidance": (
            "Cover capital contribution, profit and loss sharing ratios, "
            "management rights, admission and retirement of partners, and "
            "dissolution. Registration is optional but an unregistered firm "
            "cannot sue to enforce contracts."
        ),
    },
}


def list_types() -> List[Dict[str, Any]]:
    return [
        {
            "id": key,
            "name": meta["name"],
            "category": meta["category"],
            "fields": meta["fields"],
            "needs_advocate": meta["needs_advocate"],
        }
        for key, meta in DOC_TYPES.items()
    ]


# ---------------------------------------------------------------------------
# Grounding: pull real statute sections for this document type
# ---------------------------------------------------------------------------

# BM25 always returns its top N, even when nothing is genuinely relevant -
# there's no score floor. For Q&A that's tolerable because the synthesis agent
# discards the noise. For DRAFTING it is dangerous: handing Gemini "Section 85,
# cruelty by husband" as a citable source for a cheque bounce notice is an
# invitation to cite it. So every candidate must clear a relevance bar before
# it is offered for citation. Offering nothing is the correct outcome when the
# governing act hasn't been ingested.

MIN_TERM_OVERLAP = 2  # distinct query terms that must appear in the section


def _relevant(hit: Dict[str, Any], query: str) -> bool:
    terms = set(statutes._tokenize(query))
    if not terms:
        return False
    haystack = set(hit.get("_tokens") or [])
    overlap = terms & haystack
    if len(overlap) >= MIN_TERM_OVERLAP:
        return True
    # A single overlap counts only if it also appears in the section title -
    # that means the section is *about* the term, not merely mentioning it.
    title_terms = set(statutes._tokenize(hit.get("title") or ""))
    return bool(overlap & title_terms)


def _ground(doc_type: Optional[str], extra_text: str = "") -> List[Dict[str, Any]]:
    """Retrieve the statute sections that govern this document.

    Returns source dicts in the same shape reasoning.py uses, so citations
    carry real, openable URLs. Returns [] when nothing relevant is indexed -
    the drafting prompt handles that case by citing nothing.
    """
    queries: List[str] = []
    if doc_type and doc_type in DOC_TYPES:
        queries.extend(DOC_TYPES[doc_type]["statute_queries"])
    if extra_text:
        queries.append(extra_text[:400])

    seen = set()
    sources: List[Dict[str, Any]] = []
    for q in queries:
        for hit in statutes.search(q, limit=STATUTES_PER_QUERY):
            key = (hit["act_key"], hit["section"])
            if key in seen:
                continue
            if not _relevant(hit, q):
                logger.debug("Dropped irrelevant grounding hit: %s %s for %r",
                             hit["act_short"], hit["section"], q)
                continue
            seen.add(key)
            sources.append(statutes.as_source(hit))
    return sources


def _source_block(sources: List[Dict[str, Any]]) -> str:
    if not sources:
        return "(No statutory provisions were retrieved for this document.)"
    out = []
    for i, s in enumerate(sources, start=1):
        out.append(
            f"[{i}] {s['title']}\n"
            f"    URL: {s.get('url') or ''}\n"
            f"    TEXT: {(s.get('text') or '')[:1500]}"
        )
    return "\n\n".join(out)


def _citations(sources: List[Dict[str, Any]], used: List[int]) -> List[Dict[str, Any]]:
    out = []
    for n in used:
        if 1 <= n <= len(sources):
            s = sources[n - 1]
            out.append({
                "title": s["title"],
                "source": s.get("court") or "Bare Act",
                "url": s.get("url"),
            })
    return out


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------

_DRAFT_SYSTEM = """You are a legal drafting assistant for Nyaya Sathi, working \
under Indian law. You produce first drafts of documents from plain-English \
instructions.

Hard rules:
- Draft in the register and structure an Indian practitioner would expect, but \
keep the language as plain as the document type allows.
- Use [SQUARE BRACKETS] for every detail the user did not supply. Never invent \
names, dates, amounts, addresses, case numbers or cheque numbers. A bracket the \
user must fill is correct; a plausible-looking fabrication is not.
- Cite ONLY the numbered statutory provisions supplied to you. If you want to \
reference a provision that is not in the list, describe it in words without a \
section number. NEVER produce a section number, case name or citation from your \
own memory - a fabricated citation in a legal document causes real harm.
- If a source is marked REPEALED, use the successor act instead and say so.
- This is a draft for the user to review, not filed or executed advice.

Return ONLY a JSON object:
{
  "title": "document title as it should appear at the top",
  "body": "the complete document, plain text, \\n for line breaks, numbered clauses where appropriate",
  "used_sources": [1, 2],
  "missing_information": ["each bracketed detail the user still needs to supply"],
  "notes": ["practical warnings: registration, stamp duty, deadlines, witnessing"]
}"""


async def draft(
    doc_type: Optional[str],
    instructions: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    meta = DOC_TYPES.get(doc_type or "", {})
    sources = _ground(doc_type, instructions)

    detail_lines = ""
    if details:
        detail_lines = "\n".join(
            f"  {k}: {v}" for k, v in details.items() if v not in (None, "")
        )

    prompt = (
        f"DOCUMENT TYPE: {meta.get('name') or 'as described by the user'}\n"
        f"DRAFTING GUIDANCE: {meta.get('guidance') or 'Follow standard Indian practice.'}\n\n"
        f"USER INSTRUCTIONS:\n{instructions}\n\n"
        + (f"SUPPLIED DETAILS:\n{detail_lines}\n\n" if detail_lines else "")
        + f"STATUTORY PROVISIONS AVAILABLE FOR CITATION:\n\n{_source_block(sources)}\n\n"
        "Produce the draft now, as JSON."
    )

    data = gemini._parse_json(
        await gemini._generate(prompt, _DRAFT_SYSTEM, temperature=0.3, max_output_tokens=6000)
    )

    used = [int(n) for n in (data.get("used_sources") or []) if str(n).isdigit()]

    return {
        "title": str(data.get("title") or meta.get("name") or "Draft document").strip(),
        "body": str(data.get("body") or "").strip(),
        "citations": _citations(sources, used),
        "missing_information": [str(x) for x in (data.get("missing_information") or [])],
        "notes": [str(x) for x in (data.get("notes") or [])],
        "needs_advocate": bool(meta.get("needs_advocate")),
    }


# ---------------------------------------------------------------------------
# Red-lining
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = """You are reviewing a contract or legal document sent to the \
user by a counterparty, under Indian law. You act for the USER, not the \
counterparty. Find what is unfair, risky, unenforceable, or missing.

For each issue, give: the clause text (quoted briefly, under 25 words), why it \
is a problem in plain language, a concrete suggested replacement, and a severity.

Severity:
  high   - materially harms the user, or is unenforceable in a way that leaves
           them exposed
  medium - one-sided or unclear enough to cause a dispute
  low    - tidy-up, missing boilerplate, ambiguity

Grounding rule, absolute:
- Set "source_index" to a number from the provided list ONLY when that specific \
provision actually supports the point. Then the flag is a legal one.
- If your concern is commercial or drafting judgment rather than statute, set \
"source_index" to null and "basis" to "drafting". That is a perfectly good flag.
- NEVER cite a section number, act, or case that is not in the provided list. \
Inventing a citation is worse than giving no citation.

Also report what is MISSING - absent clauses that expose the user.

Return ONLY a JSON object:
{
  "summary": "2-3 sentences on the document's overall fairness to the user",
  "risk_level": "high" | "medium" | "low",
  "flags": [
    {
      "clause": "brief quote from the document, under 25 words",
      "issue": "plain-language explanation of the problem",
      "suggestion": "concrete replacement wording or change",
      "severity": "high",
      "basis": "statute" | "drafting",
      "source_index": 1
    }
  ],
  "missing_clauses": ["clause the document should contain but doesn't"]
}"""


async def review(
    document_text: str,
    doc_type: Optional[str] = None,
    context: Optional[str] = None,
) -> Dict[str, Any]:
    text = (document_text or "").strip()
    if not text:
        raise ValueError("There is no document text to review.")

    truncated = len(text) > MAX_DOC_CHARS
    text = text[:MAX_DOC_CHARS]

    sources = _ground(doc_type, text[:2000])

    prompt = (
        f"DOCUMENT TYPE: {DOC_TYPES.get(doc_type or '', {}).get('name') or 'unknown'}\n"
        + (f"USER'S SITUATION: {context}\n" if context else "")
        + f"\nSTATUTORY PROVISIONS AVAILABLE FOR CITATION:\n\n{_source_block(sources)}\n\n"
        f"DOCUMENT TO REVIEW:\n\n{text}\n\n"
        "Review it now, as JSON."
    )

    data = gemini._parse_json(
        await gemini._generate(prompt, _REVIEW_SYSTEM, temperature=0.15, max_output_tokens=6000)
    )

    flags = []
    for f in data.get("flags") or []:
        idx = f.get("source_index")
        citation = None
        # Only attach a citation if the index maps to a real retrieved source.
        if isinstance(idx, int) and 1 <= idx <= len(sources):
            s = sources[idx - 1]
            citation = {
                "title": s["title"],
                "source": s.get("court") or "Bare Act",
                "url": s.get("url"),
            }
        severity = str(f.get("severity", "medium")).lower()
        if severity not in ("high", "medium", "low"):
            severity = "medium"

        flags.append({
            "clause": str(f.get("clause") or "")[:300],
            "issue": str(f.get("issue") or ""),
            "suggestion": str(f.get("suggestion") or ""),
            "severity": severity,
            "basis": "statute" if citation else "drafting",
            "citation": citation,
        })

    order = {"high": 0, "medium": 1, "low": 2}
    flags.sort(key=lambda f: order[f["severity"]])

    risk = str(data.get("risk_level", "medium")).lower()
    if risk not in ("high", "medium", "low"):
        risk = "medium"

    return {
        "summary": str(data.get("summary") or "").strip(),
        "risk_level": risk,
        "flags": flags,
        "missing_clauses": [str(x) for x in (data.get("missing_clauses") or [])],
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Plain-text extraction for uploaded files
# ---------------------------------------------------------------------------

def extract_text(data: bytes, content_type: str, filename: str = "") -> str:
    """Pull text out of an uploaded document so it can be reviewed."""
    ct = (content_type or "").lower()

    if ct == "text/plain" or filename.lower().endswith(".txt"):
        return data.decode("utf-8", errors="replace")

    if ct == "application/pdf" or filename.lower().endswith(".pdf"):
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except ImportError:
            raise ValueError("PDF review needs pdfplumber: pip install pdfplumber")
        except Exception as exc:
            raise ValueError(f"Could not read that PDF: {exc}")

    if filename.lower().endswith(".docx") or "wordprocessingml" in ct:
        try:
            import docx
            import io
            document = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in document.paragraphs)
        except ImportError:
            raise ValueError("Word review needs python-docx: pip install python-docx")
        except Exception as exc:
            raise ValueError(f"Could not read that Word file: {exc}")

    raise ValueError(
        "Only PDF, Word (.docx) and plain text documents can be reviewed. "
        "Scanned images would need OCR."
    )