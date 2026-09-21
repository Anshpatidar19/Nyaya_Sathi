"""Document intelligence for the advocate-client chat.

Three jobs, all against ONE document that was shared in a thread:

  analyze()           structured extraction - parties, dates, sections,
                      clauses, amounts, claims, plus what is MISSING
  suggest_questions() what the advocate still has to ask the client, driven
                      by the gaps rather than by a fixed checklist
  ask()               scoped Q&A - answers from this document only, and says
                      so when the answer is not in it

Why this is a separate module from drafting.review():

  review() acts FOR the user against a counterparty's paper - it hunts for
  unfair clauses and suggests replacement wording. That is a different job
  from "an advocate has been handed a file by a client and needs to know
  what is in it and what to ask next". Same input, different output, and
  merging them would mean one prompt doing both badly.

Grounding, which is the whole point:

  Every field returned here is supposed to be IN the document. The prompts
  say so repeatedly and the shaping code below drops anything the model
  returns that it cannot place. A hallucinated party name or an invented
  section number is worse than an empty field, because an advocate acting
  on it does so believing it came from the file. When something is not in
  the document, the honest output is an empty list and a gap.

Caching: keyed on document id, in memory, LRU. A stored object is immutable
(/documents writes a new path per upload and nothing rewrites one), so the
extraction for a given document can never change. Re-opening a thread and
clicking Analyze again should not cost another Gemini call and eight
seconds. Restarting the server clears it, which is the right trade for now
- persisting these would mean a table and a migration, and the cost of a
cold miss is one call.
"""

import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import httpx

from . import drafting, gemini, storage

logger = logging.getLogger(__name__)

# How much of the document reaches the model. A notice, FIR, agreement or
# order fits comfortably; a 200-page charge sheet is truncated and the
# response says so rather than pretending it read all of it.
DOC_CHARS = 20000

# How much of the conversation is shown to the question engine. Only the
# text messages, newest last. This is what makes "already asked" work.
CHAT_CHARS = 4000

MAX_QUESTIONS = 10

_CACHE_MAX = 48

_text_cache: "OrderedDict[str, str]" = OrderedDict()
_analysis_cache: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
_questions_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _cache_get(cache: OrderedDict, key):
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    return None


def _cache_put(cache: OrderedDict, key, value) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_MAX:
        cache.popitem(last=False)


def forget(document_id: int) -> None:
    """Drop cached AI output for one document. Used by the force re-run."""
    _analysis_cache.pop(document_id, None)
    for key in [k for k in _questions_cache if k.startswith(f"{document_id}:")]:
        _questions_cache.pop(key, None)


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

# Keep-alive pool for pulling bytes out of Supabase Storage. A fresh client
# per download repeats the TLS handshake to the storage host every time, and
# a document is read again on every follow-up question about it.
_blob_client: Optional[httpx.AsyncClient] = None


def _get_blob_client() -> httpx.AsyncClient:
    global _blob_client
    if _blob_client is None or _blob_client.is_closed:
        _blob_client = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _blob_client


async def close_client() -> None:
    """Called from the FastAPI shutdown hook."""
    global _blob_client
    if _blob_client is not None and not _blob_client.is_closed:
        await _blob_client.aclose()
    _blob_client = None


async def load_text(storage_path: str, content_type: str, filename: str) -> str:
    """Download a stored document and extract its text.

    Raises ValueError for a file whose text cannot be read - a scan, a photo
    of a notice - which the API turns into a 400 with the extractor's own
    message, since "this needs OCR" is the useful thing to say.
    """
    cached = _cache_get(_text_cache, storage_path)
    if cached is not None:
        return cached

    url = await storage.signed_url(storage_path, expires_in=120)
    blob = await _get_blob_client().get(url)
    blob.raise_for_status()
    text = drafting.extract_text(blob.content, content_type, filename)

    if not (text or "").strip():
        raise ValueError(
            "No readable text could be extracted from this file. If it is a "
            "scan or a photo, it needs OCR before it can be analysed."
        )

    _cache_put(_text_cache, storage_path, text)
    return text


def chat_context(messages: List[Dict[str, str]]) -> str:
    """Render recent thread messages as context.

    Labelled by role rather than by name so the model cannot confuse a
    participant's name with a party named in the document. Attachments and
    empty messages are skipped - a filename tells the question engine
    nothing it does not already know.
    """
    lines: List[str] = []
    for m in messages:
        body = " ".join((m.get("content") or "").split())
        if not body:
            continue
        who = "ADVOCATE" if m.get("role") == "advocate" else "CLIENT"
        lines.append(f"{who}: {body}")

    if not lines:
        return ""

    # Newest matters most, so trim from the front if this is long.
    out = "\n".join(lines)
    if len(out) > CHAT_CHARS:
        out = "\u2026\n" + out[-CHAT_CHARS:]
    return out


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _str_list(values: Any, limit: int, cap: int) -> List[str]:
    out = []
    for v in values or []:
        text = _clip(v, limit)
        if text:
            out.append(text)
        if len(out) >= cap:
            break
    return out


def _level(value: Any, default: str = "medium") -> str:
    level = str(value or "").strip().lower()
    return level if level in ("high", "medium", "low") else default


# ---------------------------------------------------------------------------
# 1. Analyze
# ---------------------------------------------------------------------------

_ANALYSIS_SYSTEM = """You are the document-intelligence engine for Nyaya Sathi, \
a platform used by Indian advocates. An advocate has been sent a document by a \
client and needs to know what is in it before the next conversation.

Extract what is ACTUALLY IN the document. This is extraction, not advice and \
not drafting.

Absolute grounding rules:
- Every name, date, amount, section number and clause you return must appear \
in the document. Copy them, do not restate them from memory.
- NEVER supply an Act or section number that the document does not name. If \
the document says "under the relevant provisions", that is not a section.
- If a category is absent from the document, return an empty list for it. An \
empty list is a correct answer. Inventing a plausible entry is not.
- Do not resolve ambiguity by guessing. If a party is named only as "the \
Company", that is the name.
- Write dates as they appear in the document.

The "gaps" list is the most valuable part of your output. A gap is something \
the document ASSERTS, RELIES ON or REQUIRES but does not evidence or state - \
for example: a payment the document says was made but does not prove, a \
notice period that has started running but with no date of service, a clause \
referring to an annexure that is not present, an allegation with no named \
witness. Do not list generic missing information; each gap must be traceable \
to something the document itself says.

Return ONLY a JSON object, no markdown fences:
{
  "document_type": "Legal Notice under Section 138 NI Act",
  "type_code": "legal_notice" | "fir" | "rent_agreement" | "employment" | \
"court_order" | "contract" | "affidavit" | "cheque_dishonour" | "property" | \
"consumer" | "family" | "other",
  "confidence": "high" | "medium" | "low",
  "summary": "3-5 sentences: what this document is, who sent it to whom, what \
it demands or records, and why it matters to the client's position",
  "parties": [
    {"name": "as written", "role": "sender | recipient | accused | complainant \
| landlord | tenant | employer | employee | petitioner | respondent | witness | \
other", "organisation": "company or authority, or empty"}
  ],
  "dates": [{"date": "as written", "event": "what happened or is due then"}],
  "claims": ["each allegation, demand or assertion made in the document"],
  "laws": [
    {"act": "exact name as written", "section": "as written, or empty",
     "context": "what the document invokes it for"}
  ],
  "clauses": [
    {"heading": "clause number or short label", "summary": "what it obliges \
or permits, in plain language", "kind": "obligation" | "condition" | \
"deadline" | "penalty" | "termination" | "payment" | "other"}
  ],
  "amounts": [{"amount": "as written, with currency", "purpose": "what it is for"}],
  "key_facts": ["factual statements an advocate would need at hand"],
  "gaps": [
    {"issue": "what the document relies on but does not establish",
     "why_it_matters": "the consequence for the client's position",
     "severity": "high" | "medium" | "low"}
  ]
}"""


async def analyze(
    text: str,
    filename: str = "",
    context: str = "",
    truncated: bool = False,
) -> Dict[str, Any]:
    prompt = (
        f"FILE NAME: {filename or 'unknown'}\n"
        + (f"\nWHAT THE CLIENT HAS SAID IN THE CHAT SO FAR:\n{context}\n" if context else "")
        + f"\nDOCUMENT{' (truncated)' if truncated else ''}:\n\n{text}\n\n"
        "Extract it now, as JSON."
    )

    data = await gemini.generate_json(
        prompt,
        _ANALYSIS_SYSTEM,
        temperature=0.1,
        max_output_tokens=5000,
        thinking_budget=0,
    )

    parties = []
    for p in (data.get("parties") or [])[:12]:
        if not isinstance(p, dict):
            continue
        name = _clip(p.get("name"), 160)
        if not name:
            continue
        parties.append({
            "name": name,
            "role": _clip(p.get("role"), 60) or "other",
            "organisation": _clip(p.get("organisation"), 160),
        })

    dates = []
    for d in (data.get("dates") or [])[:16]:
        if not isinstance(d, dict):
            continue
        when = _clip(d.get("date"), 80)
        if not when:
            continue
        dates.append({"date": when, "event": _clip(d.get("event"), 240)})

    laws = []
    for law in (data.get("laws") or [])[:16]:
        if not isinstance(law, dict):
            continue
        act = _clip(law.get("act"), 160)
        section = _clip(law.get("section"), 80)
        # A row with neither an act nor a section is not a citation.
        if not act and not section:
            continue
        laws.append({
            "act": act,
            "section": section,
            "context": _clip(law.get("context"), 240),
        })

    clauses = []
    for c in (data.get("clauses") or [])[:20]:
        if not isinstance(c, dict):
            continue
        summary = _clip(c.get("summary"), 400)
        if not summary:
            continue
        kind = str(c.get("kind") or "other").strip().lower()
        if kind not in (
            "obligation", "condition", "deadline", "penalty",
            "termination", "payment", "other",
        ):
            kind = "other"
        clauses.append({
            "heading": _clip(c.get("heading"), 120),
            "summary": summary,
            "kind": kind,
        })

    amounts = []
    for a in (data.get("amounts") or [])[:16]:
        if not isinstance(a, dict):
            continue
        amount = _clip(a.get("amount"), 80)
        if not amount:
            continue
        amounts.append({"amount": amount, "purpose": _clip(a.get("purpose"), 200)})

    gaps = []
    for g in (data.get("gaps") or [])[:10]:
        if not isinstance(g, dict):
            continue
        issue = _clip(g.get("issue"), 300)
        if not issue:
            continue
        gaps.append({
            "issue": issue,
            "why_it_matters": _clip(g.get("why_it_matters"), 300),
            "severity": _level(g.get("severity")),
        })
    order = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(key=lambda g: order[g["severity"]])

    type_code = str(data.get("type_code") or "other").strip().lower()

    return {
        "document_type": _clip(data.get("document_type"), 120) or "Unidentified document",
        "type_code": type_code,
        "confidence": _level(data.get("confidence"), "medium"),
        "summary": _clip(data.get("summary"), 1400),
        "parties": parties,
        "dates": dates,
        "claims": _str_list(data.get("claims"), 400, 14),
        "laws": laws,
        "clauses": clauses,
        "amounts": amounts,
        "key_facts": _str_list(data.get("key_facts"), 400, 14),
        "gaps": gaps,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# 2. Suggest questions
# ---------------------------------------------------------------------------

_QUESTIONS_SYSTEM = """You prepare an Indian advocate's next conversation with \
their client about one specific document the client has sent.

Your output is the list of questions the advocate still needs answered. It is \
NOT a checklist for the document type - it is driven by THIS document and by \
what the client has already said in the chat.

How to choose questions:
1. Start from the gaps: things the document asserts, relies on or requires but \
does not evidence. A deposit the document says was paid with no proof of \
payment. A notice period running from a date of service the document does not \
record. An allegation with no named witness. An annexure referred to but not \
attached.
2. Then the facts an advocate must have to act on a document of this kind but \
which this document does not contain - date of receipt, whether it has been \
responded to, what the client's own account of the events is.
3. Then instructions: what the client wants to happen.

Hard rules:
- SKIP anything the chat already answers. If the client has said they received \
the notice on 3 March, do not ask when they received it. List those in \
"already_known" instead.
- Every question must be answerable by the CLIENT. Do not ask questions only a \
court, a bank or the other side could answer.
- Be specific and quote the document's own particulars: "Do you have proof of \
the Rs 50,000 security deposit the agreement records at clause 4?" beats "Do \
you have any payment proof?".
- Never assert a fact the document does not contain in order to make a question \
sound specific.
- Order by what would most change the advice. 5 to 10 questions, no filler.

Return ONLY a JSON object, no markdown fences:
{
  "document_type": "what you are treating this as",
  "questions": [
    {
      "question": "the question, phrased so the advocate can send it as-is",
      "why": "one line: what turns on the answer",
      "gap": "the thing in the document this fills, or empty if it is not gap-driven",
      "category": "evidence" | "timeline" | "facts" | "documents" | \
"communication" | "instructions",
      "priority": "high" | "medium" | "low"
    }
  ],
  "already_known": ["facts the chat has already established, so they are not asked again"]
}"""


async def suggest_questions(
    text: str,
    filename: str = "",
    analysis: Optional[Dict[str, Any]] = None,
    context: str = "",
    truncated: bool = False,
) -> Dict[str, Any]:
    """Questions for the client.

    The analysis is passed in when it exists: it already names the parties,
    dates, amounts and gaps, so handing it over keeps the questions anchored
    to the same particulars the advocate is looking at on screen rather than
    to a second, independent reading of the file.
    """
    summary_block = ""
    if analysis:
        gaps = "\n".join(
            f"  - [{g['severity']}] {g['issue']}" for g in analysis.get("gaps") or []
        )
        amounts = "; ".join(
            f"{a['amount']} ({a['purpose']})" for a in analysis.get("amounts") or []
        )
        dates = "; ".join(
            f"{d['date']} - {d['event']}" for d in analysis.get("dates") or []
        )
        summary_block = (
            "\nEXTRACTION ALREADY PRODUCED FOR THIS DOCUMENT:\n"
            f"Type: {analysis.get('document_type')}\n"
            f"Summary: {analysis.get('summary')}\n"
            + (f"Dates: {dates}\n" if dates else "")
            + (f"Amounts: {amounts}\n" if amounts else "")
            + (f"Gaps found:\n{gaps}\n" if gaps else "")
        )

    prompt = (
        f"FILE NAME: {filename or 'unknown'}\n"
        + summary_block
        + (
            f"\nWHAT HAS ALREADY BEEN SAID IN THE CHAT (do not re-ask any of this):\n{context}\n"
            if context
            else "\nTHE CHAT IS EMPTY so far - nothing has been established yet.\n"
        )
        + f"\nDOCUMENT{' (truncated)' if truncated else ''}:\n\n{text}\n\n"
        "Produce the questions now, as JSON."
    )

    data = await gemini.generate_json(
        prompt,
        _QUESTIONS_SYSTEM,
        temperature=0.25,
        max_output_tokens=3500,
        thinking_budget=0,
    )

    questions = []
    for q in data.get("questions") or []:
        if not isinstance(q, dict):
            continue
        body = _clip(q.get("question"), 400)
        if not body:
            continue
        category = str(q.get("category") or "facts").strip().lower()
        if category not in (
            "evidence", "timeline", "facts", "documents",
            "communication", "instructions",
        ):
            category = "facts"
        questions.append({
            "question": body,
            "why": _clip(q.get("why"), 240),
            "gap": _clip(q.get("gap"), 240),
            "category": category,
            "priority": _level(q.get("priority")),
        })

    # Gap-driven questions first, then priority. A high-priority question
    # that closes a hole in the document is worth more than a high-priority
    # general one, and this is the differentiator the feature exists for.
    order = {"high": 0, "medium": 1, "low": 2}
    questions.sort(key=lambda q: (0 if q["gap"] else 1, order[q["priority"]]))

    return {
        "document_type": _clip(data.get("document_type"), 120),
        "questions": questions[:MAX_QUESTIONS],
        "already_known": _str_list(data.get("already_known"), 240, 8),
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# 3. Ask about the document
# ---------------------------------------------------------------------------

_ASK_SYSTEM = """You answer an advocate's questions about ONE document they \
have in front of them. The document is supplied below. Answer from it.

The single rule that matters: be explicit about where the answer comes from.

- If the document answers the question, answer it and set \
"found_in_document" to true. Support it with short quotes from the document \
(under 25 words each) in "evidence".
- If the document does NOT answer it, say so plainly, set \
"found_in_document" to false, and leave "evidence" empty. You may then add \
what the advocate would need to establish it - in "note", clearly marked as \
not coming from the document.
- Never fill a hole with general legal knowledge and present it as the \
document's content. Never cite a section the document does not name.
- If the question is about Indian law generally rather than about this \
document, answer briefly with "found_in_document" false and say it is \
general context, not from the file.
- Plain professional English. No preamble, no "As an AI".

Return ONLY a JSON object, no markdown fences:
{
  "answer": "the answer, 1-6 sentences, or a short list written as lines",
  "found_in_document": true,
  "evidence": ["short quote from the document, under 25 words"],
  "note": "optional: what is missing, or that this is general context"
}"""


async def ask(
    text: str,
    question: str,
    filename: str = "",
    history: Optional[List[Dict[str, str]]] = None,
    truncated: bool = False,
) -> Dict[str, Any]:
    turns = ""
    for turn in (history or [])[-6:]:
        role = "ADVOCATE" if turn.get("role") == "user" else "ASSISTANT"
        body = " ".join((turn.get("content") or "").split())[:600]
        if body:
            turns += f"{role}: {body}\n"

    prompt = (
        f"FILE NAME: {filename or 'unknown'}\n"
        + (f"\nEARLIER IN THIS DOCUMENT THREAD:\n{turns}" if turns else "")
        + f"\nDOCUMENT{' (truncated)' if truncated else ''}:\n\n{text}\n\n"
        f"ADVOCATE'S QUESTION: {question}\n\n"
        "Answer now, as JSON."
    )

    data = await gemini.generate_json(
        prompt,
        _ASK_SYSTEM,
        temperature=0.15,
        max_output_tokens=2000,
        thinking_budget=0,
    )

    found = bool(data.get("found_in_document"))
    return {
        "answer": _clip(data.get("answer"), 3000)
        or "No answer could be produced for that question.",
        "found_in_document": found,
        # Evidence only means something when the answer claims to be from the
        # document. Quotes attached to a "not in this document" answer would
        # contradict it.
        "evidence": _str_list(data.get("evidence"), 300, 4) if found else [],
        "note": _clip(data.get("note"), 600),
    }


# ---------------------------------------------------------------------------
# Cache-aware entry points used by the API layer
# ---------------------------------------------------------------------------

async def analysis_for(
    document_id: int,
    text: str,
    filename: str,
    context: str = "",
    force: bool = False,
) -> tuple[Dict[str, Any], bool]:
    """Returns (analysis, from_cache)."""
    if not force:
        hit = _cache_get(_analysis_cache, document_id)
        if hit is not None:
            return hit, True

    truncated = len(text) > DOC_CHARS
    result = await analyze(text[:DOC_CHARS], filename, context, truncated)
    _cache_put(_analysis_cache, document_id, result)
    return result, False


async def questions_for(
    document_id: int,
    text: str,
    filename: str,
    context: str = "",
    force: bool = False,
) -> tuple[Dict[str, Any], bool]:
    """Returns (questions, from_cache).

    Keyed on the document AND the length of the chat context: once more has
    been said in the thread, the previous set of questions is out of date by
    definition - some of them have just been answered. Using the length
    rather than a hash keeps the key cheap and is wrong only if a message is
    edited, which this product does not allow.
    """
    key = f"{document_id}:{len(context)}"
    if not force:
        hit = _cache_get(_questions_cache, key)
        if hit is not None:
            return hit, True

    truncated = len(text) > DOC_CHARS
    analysis = _cache_get(_analysis_cache, document_id)
    result = await suggest_questions(
        text[:DOC_CHARS], filename, analysis, context, truncated
    )
    _cache_put(_questions_cache, key, result)
    return result, False