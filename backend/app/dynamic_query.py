"""Query-driven retrieval for Review, Drafting, Arguments and document Q&A.

What this replaces
------------------
Several features used to search with text that had nothing to do with what
the user actually sent:

  Review      the same hardcoded per-document-type queries every time
              ("lease of immoveable property", "tenancy rights") plus the
              first 2,000 characters of the document as one BM25 query -
              which for a rent agreement is the cause title and the parties'
              addresses, not the clauses worth checking
  Drafting    the same hardcoded per-type queries, whatever was asked for
  Arguments   the issue plus facts[:600] - the opening of the pleading
  Ask + file  "Explain this document and what it means for me." as the
              retrieval text whenever the document cites no section

Now every query is built from the request itself:

  1. the user's own words (instruction, context, issue), searched with the
     full statutes.search - exact citations, BM25, dense fallback
  2. provisions the document or facts NAME ("Section 138 of the NI Act"),
     looked up exactly
  3. the document's most legally-loaded passages - each clause is scored by
     how much statute vocabulary it carries (IDF over the bare-act corpus),
     and the top few become their own keyword queries

Hits from every query are fused by reciprocal rank, so a section that
several parts of the document point at outranks one that a single stray
word matched. Every candidate still has to clear a term-overlap relevance
gate before it can be offered for citation - offering nothing is better than
offering a wrong section to a model that will cite it.

Cost: no model call. Passage queries use BM25 only (no dense fallback), so
a long document never multiplies embedding calls; only the user's own query
can trigger the dense fallback, exactly as /ask does.

Document-type queries: drafting's per-type lists are no longer the search.
When the user picks a type they join the fusion at the lowest weight, so they
can fill a gap the request leaves (the corpus has no labour or tenancy act,
for instance) but never outrank what the request itself points at.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import statutes

logger = logging.getLogger(__name__)

# How much of a document is scanned for passages. Past this, an agreement is
# schedules and signature blocks.
SCAN_CHARS = 40000
PASSAGE_MAX_CHARS = 700
PASSAGE_MIN_CHARS = 60
TERMS_PER_PASSAGE = 8
DEFAULT_PASSAGES = 5

# Reciprocal-rank constant. 60 is the usual value; smaller lets rank 1 of a
# single query dominate, which is not what fusion is for.
RRF_K = 20

# Query-kind weights. The user's own words say what they want; a provision
# the document names is almost certainly relevant; a passage query is the
# weakest signal and needs agreement from other passages to rank high.
WEIGHTS = {"user": 1.6, "citation": 2.0, "passage": 1.0, "static": 0.6}

# Words that carry no legal signal in a user instruction or a contract but
# are not in statutes._STOPWORDS (which is tuned for the bare acts).
_FILLER = {
    "please", "check", "review", "explain", "document", "documents", "file",
    "attached", "tell", "help", "need", "want", "legitimacy", "legit", "ok",
    "okay", "whether", "mean", "means", "meaning", "me", "us", "our", "we",
    "your", "yours", "hereby", "herein", "hereinafter", "hereto", "thereof",
    "whereas", "witnesseth", "said", "above", "below", "following", "also",
    "will", "would", "should", "must", "each", "every", "all", "other",
    "same", "within", "without", "between", "from", "into", "upon", "after",
    "before", "during", "till", "until", "per", "etc", "i", "e", "g", "mr",
    "mrs", "ms", "sri", "shri", "smt", "dated", "date", "day", "page", "name",
    "address", "resident", "son", "daughter", "wife", "aged", "years", "year",
    "one", "two", "three", "first", "second", "third", "party", "parties",
    "signed", "signature", "witness", "witnesses", "place", "schedule",
    "draft", "drafting", "prepare", "write", "make", "create", "legal",
    "send", "give", "kindly", "regarding", "sir", "madam", "non",
}

_GENERIC_DOC_Q_RE = re.compile(
    r"^\s*(please\s+)?(explain|summari[sz]e|review|check|analy[sz]e|read|"
    r"what\s+(does|is)\s+(this|it)|what\s+it\s+means|is\s+this\s+(legal|valid|legit))\b",
    re.IGNORECASE,
)

_SPLIT_RE = re.compile(
    r"\n\s*\n"                                   # blank line
    r"|\n\s*(?=(?:\d{1,2}(?:\.\d{1,2})*[.)]\s)"   # "3. " / "3.1 " / "3) "
    r"|(?:\([a-z0-9ivx]{1,4}\)\s)"               # "(a) " / "(iv) "
    r"|(?:clause|article|section)\s+\d)",         # "Clause 7"
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.;:])\s+(?=[A-Z(\"'])")


# ---------------------------------------------------------------------------
# Term statistics
# ---------------------------------------------------------------------------

def _stats() -> Tuple[int, Dict[str, int]]:
    idx = statutes._index()
    return len(idx["docs"]) or 1, idx["df"]


def _idf(term: str, n: int, df: Dict[str, int]) -> float:
    d = df.get(term, 0)
    return math.log(1 + (n - d + 0.5) / (d + 0.5))


def _content_tokens(text: str) -> List[str]:
    return [
        t for t in statutes._tokenize(text or "")
        if t not in _FILLER and len(t) > 2 and not t.isdigit()
    ]


def key_terms(text: str, k: int = 10) -> List[str]:
    """The words in `text` that the statute corpus can actually match on,
    most distinctive first.

    A term must appear in at least one bare-act section (otherwise BM25
    cannot use it) and in no more than a third of them (otherwise it
    matches everything). Repetition helps a little, rarity helps more.
    """
    n, df = _stats()
    counts = Counter(_content_tokens(text))
    scored = []
    for term, tf in counts.items():
        d = df.get(term, 0)
        if d == 0 or d > n / 3:
            continue
        scored.append((min(tf, 4) ** 0.5 * _idf(term, n, df), term))
    scored.sort(reverse=True)
    return [t for _, t in scored[:k]]


def is_generic_request(text: str) -> bool:
    """"Explain this document", "review this", "is this legal?" - true when
    the words carry no retrievable subject of their own."""
    text = (text or "").strip()
    if not text:
        return True
    if _GENERIC_DOC_Q_RE.match(text) and len(key_terms(text, 3)) <= 1:
        return True
    return not key_terms(text, 1)


# ---------------------------------------------------------------------------
# Passages
# ---------------------------------------------------------------------------

def passages(text: str) -> List[str]:
    """Split a document into clause-sized passages."""
    body = (text or "")[:SCAN_CHARS]
    out: List[str] = []
    for block in _SPLIT_RE.split(body):
        block = " ".join((block or "").split())
        if len(block) < PASSAGE_MIN_CHARS:
            continue
        if len(block) <= PASSAGE_MAX_CHARS:
            out.append(block)
            continue
        # Long block: pack sentences into windows.
        window = ""
        for sentence in _SENTENCE_RE.split(block):
            if window and len(window) + len(sentence) > PASSAGE_MAX_CHARS:
                out.append(window)
                window = ""
            window = f"{window} {sentence}".strip()
        if len(window) >= PASSAGE_MIN_CHARS:
            out.append(window)
    return out


def salient_passages(text: str, n: int = DEFAULT_PASSAGES) -> List[str]:
    """The n passages carrying the most statute vocabulary.

    Density, not raw sum, so a long boilerplate paragraph does not win on
    length alone; near-duplicates (the same clause repeated in a schedule)
    are skipped.
    """
    num, df = _stats()
    scored = []
    for p in passages(text):
        terms = {t for t in _content_tokens(p) if 0 < df.get(t, 0) <= num / 3}
        if len(terms) < 3:
            continue
        weight = sum(_idf(t, num, df) for t in terms) / math.sqrt(len(terms) + 4)
        scored.append((weight, p, terms))
    scored.sort(key=lambda x: x[0], reverse=True)

    chosen: List[Tuple[str, set]] = []
    for _, p, terms in scored:
        if any(len(terms & other) / max(1, len(terms | other)) > 0.6 for _, other in chosen):
            continue
        chosen.append((p, terms))
        if len(chosen) >= n:
            break
    return [p for p, _ in chosen]


# ---------------------------------------------------------------------------
# Query plan
# ---------------------------------------------------------------------------

_CITES_PROVISION_RE = re.compile(
    r"\b(section|sections|sec|s\.|u/s|article|art|order|rule)\b", re.IGNORECASE
)
_BARE_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d[\d,./-]*")


def _user_query(text: str) -> str:
    """The user's words, safe to send to statutes.search.

    statutes.lookup_section treats any number as a possible section, so
    "unpaid salary for 3 months" came back as Section 3 of every act. A
    number only means a provision when the text says section / article;
    otherwise amounts, dates and durations are removed before searching.
    Real citations are still picked up separately as a citation query.
    """
    if not _CITES_PROVISION_RE.search(text):
        text = _BARE_NUMBER_RE.sub(" ", text)
    # "Draft a legal notice to..." - the drafting verbs say what to produce,
    # not what law governs it, and "legal notice" dragged in the NI Act's
    # notice-of-dishonour sections for a salary claim.
    words = [w for w in text.split() if w.lower().strip(".,;:!?\"'()") not in _FILLER]
    return " ".join(words) or text


@dataclass
class Query:
    text: str
    kind: str          # user | citation | passage | static
    weight: float


def _citation_text(text: str, limit: int = 6) -> str:
    # Lazy import: reasoning imports a lot, and it may import this module.
    from .reasoning import document_query
    return document_query(text or "", limit=limit)


def build_queries(
    user_text: Optional[str] = None,
    document_text: Optional[str] = None,
    *,
    max_passages: int = DEFAULT_PASSAGES,
    static_queries: Sequence[str] = (),
) -> List[Query]:
    """Everything worth searching for this request, strongest signal first.

    static_queries are appended last and marked "static"; retrieve()
    uses them only if nothing dynamic produced a relevant hit.
    """
    plan: List[Query] = []
    seen = set()

    def add(text: str, kind: str) -> None:
        text = " ".join((text or "").split())[:400]
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            plan.append(Query(text, kind, WEIGHTS[kind]))

    focus_terms: List[str] = []
    if document_text:
        focus = " ".join(salient_passages(document_text, max_passages)) or document_text
        focus_terms = key_terms(focus, 5)

    if user_text and not is_generic_request(user_text):
        query = _user_query(user_text)
        if focus_terms:
            # A question about an attached document is read in its context:
            # "can they take my house without court?" alone matched the
            # Constitution's articles on the Houses of Parliament; beside
            # "borrower lender possession" it is a question about a loan.
            query = f"{query} {' '.join(focus_terms)}"
        add(query, "user")
        cites = _citation_text(user_text, limit=4)
        if cites:
            add(cites, "citation")

    if document_text:
        cites = _citation_text(document_text)
        if cites:
            add(cites, "citation")
        chosen = salient_passages(document_text, max_passages)
        per_passage = [key_terms(p, TERMS_PER_PASSAGE + 4) for p in chosen]
        # Words in most passages ("landlord", "tenant") say what the whole
        # document is about, not what this clause is about. Left in, every
        # passage query matches the same generic landlord-tenant section and
        # fusion ranks it first. Dropped from each passage query (kept only
        # if nothing else would remain); the subject is still searched once,
        # through the document-level query below.
        seen_in = Counter(t for terms in per_passage for t in set(terms))
        subject = {t for t, c in seen_in.items() if len(chosen) >= 3 and c > len(chosen) / 2}
        for terms in per_passage:
            specific = [t for t in terms if t not in subject]
            use = (specific if len(specific) >= 2 else terms)[:TERMS_PER_PASSAGE]
            if len(use) >= 2:
                add(" ".join(use), "passage")
        if subject:
            add(" ".join(sorted(subject)), "passage")

    for q in static_queries:
        add(q, "static")
    return plan


def retrieval_text(user_text: Optional[str], document_text: Optional[str], k: int = 10) -> str:
    """One query string for a single-query search (Kanoon, the Ask
    pipeline): the user's own words when they carry a subject, then the
    provisions the document names, then its most distinctive terms."""
    parts: List[str] = []
    if user_text and not is_generic_request(user_text):
        parts.append(user_text.strip())
    if document_text:
        cites = _citation_text(document_text, limit=4)
        if cites:
            parts.append(cites)
        # Terms from the most legally-loaded clauses, not the whole file:
        # the whole file's rarest words are job titles and place names.
        focus = " ".join(salient_passages(document_text)) or document_text
        parts.append(" ".join(key_terms(focus, k)))
    return " ".join(p for p in parts if p).strip()[:400]


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

MIN_TERM_OVERLAP = 2


# A keyword query built from a passage has no phrasing to lean on, so it has
# to share a real part of its vocabulary with the section: at least this
# fraction of its terms, as well as MIN_TERM_OVERLAP of them.
PASSAGE_MIN_COVERAGE = 0.34


def relevant(hit: Dict[str, Any], query: str, kind: str = "user") -> bool:
    """Does this section actually share subject matter with the query?

    BM25 always returns its top N even when nothing fits. Two distinct
    shared terms, or one shared term that is in the section's title (the
    section is ABOUT it), clears the bar for the user's own words. Passage
    queries, and user queries of four or more words, must also cover a third
    of their terms - two generic words in common ("tenant", "rent") is how a
    burden-of-proof section in the Evidence Act ended up offered for a rent
    agreement. Exact citation hits are not gated.
    """
    terms = set(statutes._expand(query))
    if not terms:
        return False
    haystack = set(hit.get("_tokens") or [])
    overlap = terms & haystack
    if kind == "passage":
        base = set(statutes._tokenize(query))
        covered = len(base & haystack) / max(1, len(base))
        return len(overlap) >= MIN_TERM_OVERLAP and covered >= PASSAGE_MIN_COVERAGE
    base = set(statutes._tokenize(query))
    if len(base) >= 4:
        # A longer question has to be matched on more than two of its words,
        # or "penalty" plus one filler word pulls in every penalty section.
        covered = len(base & haystack) / len(base)
        return len(overlap) >= MIN_TERM_OVERLAP and covered >= PASSAGE_MIN_COVERAGE
    if len(overlap) >= MIN_TERM_OVERLAP:
        return True
    title_terms = set(statutes._tokenize(hit.get("title") or ""))
    return bool(overlap & title_terms)


def _search_one(q: Query, per_query: int) -> List[Dict[str, Any]]:
    if q.kind == "citation":
        return statutes.lookup_section(q.text)
    if q.kind == "user":
        # The full search - exact citations, BM25, and the dense fallback if
        # it is switched on - same as /ask.
        return statutes.search(q.text, limit=per_query)
    # Passage and static queries: BM25 only. A long document would otherwise
    # turn into one embedding call per passage.
    exact = statutes.lookup_section(q.text)
    if exact:
        return exact[:per_query]
    return [d for _, d in statutes._bm25_scored(q.text, statutes._index()["docs"], per_query)]


def retrieve(
    plan: Iterable[Query],
    limit: int = 6,
    per_query: int = 3,
) -> List[Dict[str, Any]]:
    """Run the plan and fuse the results. Returns statute docs (the dicts
    statutes.search returns), best first, at most `limit`."""
    plan = list(plan)
    static = [q for q in plan if q.kind == "static"]

    def fuse(queries: List[Query]) -> List[Dict[str, Any]]:
        scores: Dict[Tuple[str, str], float] = {}
        docs: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for q in queries:
            try:
                hits = _search_one(q, per_query)
            except Exception as exc:
                logger.warning("Dynamic query failed (%s): %r", exc, q.text[:60])
                continue
            for rank, hit in enumerate(hits):
                key = (hit["act_key"], hit["section"])
                if q.kind != "citation" and not relevant(hit, q.text, q.kind):
                    continue
                scores[key] = scores.get(key, 0.0) + q.weight / (RRF_K + rank + 1)
                docs[key] = hit
        ranked = sorted(scores, key=lambda k: scores[k], reverse=True)
        return [docs[k] for k in ranked[:limit]]

    # Static queries (the document type's governing law, when the user
    # picked a type) are part of the fusion at the lowest weight: they can
    # fill a gap the request leaves, but a section the request itself points
    # at always outranks them.
    found = fuse(plan)

    logger.info(
        "DYNAMIC_SEARCH queries=%d (user=%d citation=%d passage=%d static=%d) -> %d sections: %s",
        len(plan),
        sum(q.kind == "user" for q in plan),
        sum(q.kind == "citation" for q in plan),
        sum(q.kind == "passage" for q in plan),
        len(static),
        len(found),
        ", ".join(f"{d['act_short']} {d['section']}" for d in found) or "-",
    )
    return found


def kanoon_query(user_text: Optional[str], document_text: Optional[str] = None,
                 k: int = 8) -> str:
    """A case-law search string built from this request, not a truncation
    of it. Kanoon is metered per search, so there is still exactly one."""
    from .reasoning import refine_query
    return refine_query(retrieval_text(user_text, document_text, k) or (user_text or ""), None)