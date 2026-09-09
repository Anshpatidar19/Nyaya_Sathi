"""Context-aware chunking for the vector index.

Two rules drive everything here:

1. A chunk must be understandable on its own. Retrieval returns chunks, not
   documents, so a chunk that begins "However, this principle applies only
   when..." is worse than useless - the model reads it as a complete thought
   and answers from a fragment. Every chunk therefore carries a header naming
   its source, and continuation chunks carry the sentence they continue from.

2. Structure beats token counts. A statute section is already the unit the
   law is written in; splitting it at 500 tokens would cut provisos away from
   the provision they qualify. We only split when a section is genuinely too
   long, and then only at boundaries the drafter put there - sub-clauses,
   provisos, explanations, illustrations.

Statutes almost never split: the longest section in the corpus is under 4,000
characters. The splitting logic exists for the few that would, and for state
codes ingested later. Judgments always split, because they are long and
because Kanoon returns them as a flat run of paragraphs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# A section longer than this gets split. Chosen well above the longest section
# in the current corpus (IPC 375, ~2,200 chars) so nothing splits today.
MAX_STATUTE_CHARS = 6000

# Target size for a judgment chunk. Small enough that several fit in a prompt,
# large enough to hold a complete piece of reasoning.
JUDGMENT_TARGET_CHARS = 1400
JUDGMENT_MAX_CHARS = 2200

# Boundaries a drafter actually put in the text. Splitting anywhere else risks
# severing a qualifier from what it qualifies.
#
# The ingested acts arrive as a single unbroken line - no newlines at all - so
# an anchor-based pattern silently never fires and long sections come through
# whole. These alternatives catch sub-clause markers mid-line, but only after
# sentence-ending punctuation, so a cross-reference like "section 3(5)" is not
# mistaken for the start of sub-clause (5).
_STATUTE_BOUNDARY = re.compile(
    r"(?:(?<=[.;:\-])\s+|^\s*)"
    r"(?=(?:\(\d+\)|\([a-z]\)|\([ivx]+\)|Provided\b|Explanation\b|"
    r"Illustration[s]?\b|Exception\b))",
    re.MULTILINE | re.IGNORECASE,
)

# Last resort when a single unit is still too big: split between sentences.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.;])\s+(?=[A-Z(])")

# text-embedding-004 accepts 2,048 tokens. At roughly 4 characters per token
# that is ~8,000 characters; we stay under it with room for the header. Text
# beyond this is not dropped - it moves into the next chunk.
EMBED_HARD_CEILING = 6000

# A unit that must never stand alone - it modifies whatever came before it.
_DEPENDENT_UNIT = re.compile(
    r"^\s*(?:Provided\b|Explanation\b|Illustration[s]?\b|Exception\b)", re.IGNORECASE
)

# A paragraph opening this way is continuing an argument. If it starts a chunk,
# the chunk needs the paragraph before it or the meaning inverts.
_CONTINUATION = re.compile(
    r"^\s*(?:However|But|Nevertheless|Nonetheless|Thus|Therefore|Hence|"
    r"Accordingly|Consequently|This\s+(?:principle|proposition|rule|view|"
    r"reasoning|test|ratio)|That\s+(?:being|apart)|Such\s|It\s+follows|"
    r"On\s+the\s+other\s+hand|In\s+that\s+view|Applying\s+(?:this|the)|"
    r"The\s+said\b|The\s+above\b|In\s+other\s+words)",
    re.IGNORECASE,
)

_WS = re.compile(r"[ \t]+")


@dataclass
class Chunk:
    """One vector's worth of content, plus everything needed to trace it back."""

    chunk_id: str
    parent_id: str
    text: str                      # what gets shown to the model / the user
    embed_text: str                # what gets embedded (header + text)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """Stable fingerprint of the content. The update agent compares this
        rather than diffing text, so an unchanged section is skipped without
        an embedding call."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


def _clean(s: str) -> str:
    return _WS.sub(" ", (s or "").strip())


def _split_preserving_units(body: str) -> List[str]:
    """Split at drafting boundaries, then re-attach anything that cannot
    stand alone to the unit before it."""
    parts = [p for p in _STATUTE_BOUNDARY.split(body) if p.strip()]
    if len(parts) <= 1:
        return [body]

    merged: List[str] = []
    for part in parts:
        # A proviso/explanation/illustration always belongs to what precedes it,
        # unless attaching it would push the unit past what we can embed.
        attachable = merged and len(merged[-1]) + len(part) < EMBED_HARD_CEILING
        if attachable and _DEPENDENT_UNIT.match(part):
            merged[-1] = merged[-1].rstrip() + "\n" + part.strip()
        # A fragment too short to be meaningful on its own also merges back.
        elif attachable and len(part.strip()) < 200:
            merged[-1] = merged[-1].rstrip() + "\n" + part.strip()
        else:
            merged.append(part.strip())
    return _enforce_ceiling(merged)


def _enforce_ceiling(units: List[str]) -> List[str]:
    """No unit may exceed what the embedding model accepts. A sub-clause that
    is itself enormous - CrPC 167 runs to 19,000 characters as one line - gets
    split between sentences, which is the least bad remaining boundary."""
    out: List[str] = []
    for u in units:
        if len(u) <= EMBED_HARD_CEILING:
            out.append(u)
            continue
        buf, size = [], 0
        for sent in _SENTENCE_BOUNDARY.split(u):
            if buf and size + len(sent) > EMBED_HARD_CEILING:
                out.append(" ".join(buf))
                buf, size = [], 0
            buf.append(sent)
            size += len(sent) + 1
        if buf:
            out.append(" ".join(buf))
    return out


def _pack(units: List[str], target: int, hard_max: int) -> List[List[str]]:
    """Greedily group units into chunks near `target`, never exceeding
    `hard_max` unless a single unit is itself larger."""
    groups: List[List[str]] = []
    cur: List[str] = []
    size = 0
    for u in units:
        n = len(u)
        if cur and (size + n > hard_max or size >= target):
            groups.append(cur)
            cur, size = [], 0
        cur.append(u)
        size += n
    if cur:
        groups.append(cur)
    return groups


# --- statutes -------------------------------------------------------------

def chunk_statute(doc: Dict[str, Any], *, version: int = 1) -> List[Chunk]:
    """Chunk one normalised statute section.

    `doc` is what statutes._normalize() produces, so this stays in step with
    the BM25 index and there is exactly one definition of a section's identity.
    Returns a single chunk for almost every section.
    """
    act_key = doc["act_key"]
    section = doc["section"]
    unit = doc.get("unit", "Section")
    title = doc.get("title") or ""
    chapter = doc.get("chapter") or ""
    body = (doc.get("text") or "").strip()
    if not body:
        return []

    parent_id = f"statute:{act_key}:{section}"

    # The header is prepended to the embedded text AND kept out of the stored
    # text, so the model sees clean statute language while the vector still
    # knows which act it came from. Chapter is included because it is what
    # lets "fundamental rights" find Article 21, which contains neither word -
    # the same reasoning as the BM25 chunk.
    def header(part: Optional[str] = None) -> str:
        bits = [doc.get("act_short") or act_key, f"{unit} {section}"]
        if title:
            bits.append(title)
        if chapter:
            bits.append(f"({chapter})")
        if part:
            bits.append(part)
        return " — ".join(bits[:2]) + (" — " + " ".join(bits[2:]) if bits[2:] else "")

    if len(body) <= MAX_STATUTE_CHARS:
        groups = [[body]]
    else:
        units = _split_preserving_units(body)
        groups = _pack(units, MAX_STATUTE_CHARS, EMBED_HARD_CEILING)

    total = len(groups)
    out: List[Chunk] = []
    for i, group in enumerate(groups):
        text = "\n".join(group).strip()
        part = f"part {i + 1} of {total}" if total > 1 else None
        chunk = Chunk(
            chunk_id=f"{parent_id}:v{version}:{i}",
            parent_id=parent_id,
            text=text,
            embed_text=f"{header(part)}\n\n{text}",
            metadata=_statute_metadata(doc, version=version, index=i, count=total),
        )
        chunk.metadata["content_hash"] = chunk.content_hash()
        out.append(chunk)
    return out


def _statute_metadata(doc: Dict[str, Any], *, version: int, index: int, count: int):
    current = bool(doc.get("current", True))
    return {
        "source_type": "statute",
        "act_key": doc["act_key"],
        "act": doc.get("act") or "",
        "act_short": doc.get("act_short") or "",
        "section": str(doc["section"]),
        "unit": doc.get("unit", "Section"),
        "title": doc.get("title") or "",
        "chapter": doc.get("chapter") or "",
        "jurisdiction": doc.get("jurisdiction") or "IN",
        "is_current": current,
        "legal_status": "active" if current else "repealed",
        "superseded_by": doc.get("superseded_by") or "",
        "url": doc.get("url") or "",
        "version": version,
        # Dates as YYYYMMDD ints so Pinecone can range-filter them. The open
        # end is a sentinel rather than a null, because a missing field and an
        # "still in force" field must not look the same to a filter.
        "effective_from": int(doc.get("effective_from") or 0),
        "effective_to": int(doc.get("effective_to") or 99991231),
        "parent_id": f"statute:{doc['act_key']}:{doc['section']}",
        "chunk_index": index,
        "chunk_count": count,
        "text": doc.get("text") or "",
    }


# --- judgments ------------------------------------------------------------

def chunk_judgment(
    docid: str,
    paragraphs: Iterable[str],
    *,
    case_name: str = "",
    court: str = "",
    court_level: str = "",
    date: str = "",
    jurisdiction: str = "IN",
    citation: str = "",
    version: int = 1,
) -> List[Chunk]:
    """Chunk a judgment so no chunk depends on one that wasn't retrieved.

    Two mechanisms do that work:

    * Every chunk is prefixed with case name, court and date. A chunk pulled
      out of context still announces what it is.
    * If a chunk would open on a continuation ("However, this applies only
      when..."), the preceding paragraph is carried in with it. That is the
      "Chunk 3 without Chunk 2" failure, and this is where it gets fixed.

    Overlap is applied only when that test fires, so ordinary chunks are not
    duplicated for nothing.
    """
    paras = [_clean(p) for p in paragraphs]
    paras = [p for p in paras if len(p) > 40]
    if not paras:
        return []

    parent_id = f"judgment:{docid}"
    groups = _pack(paras, JUDGMENT_TARGET_CHARS, JUDGMENT_MAX_CHARS)

    head_bits = [b for b in (case_name, court, date) if b]
    header = " | ".join(head_bits) if head_bits else f"Judgment {docid}"

    out: List[Chunk] = []
    cursor = 0  # index into `paras` of the first paragraph of this group
    total = len(groups)
    for i, group in enumerate(groups):
        carried = ""
        if i > 0 and _CONTINUATION.match(group[0]):
            prev = paras[cursor - 1]
            # Carry the tail of the previous paragraph, not the whole thing -
            # enough to anchor the pronoun, not enough to double the index.
            carried = prev if len(prev) <= 400 else "… " + prev[-400:]

        body = "\n\n".join(group)
        text = (carried + "\n\n" + body).strip() if carried else body
        chunk = Chunk(
            chunk_id=f"{parent_id}:v{version}:{i}",
            parent_id=parent_id,
            text=text,
            embed_text=f"{header}\n\n{text}",
            metadata={
                "source_type": "judgment",
                "docid": str(docid),
                "case_name": case_name or "",
                "citation": citation or "",
                "court": court or "",
                "court_level": court_level or "",
                "jurisdiction": jurisdiction or "IN",
                "date": date or "",
                "date_num": _date_num(date),
                "is_current": True,
                "legal_status": "active",
                "version": version,
                "effective_from": _date_num(date),
                "effective_to": 99991231,
                "parent_id": parent_id,
                "chunk_index": i,
                "chunk_count": total,
                "has_carried_context": bool(carried),
                "text": text,
            },
        )
        chunk.metadata["content_hash"] = chunk.content_hash()
        out.append(chunk)
        cursor += len(group)
    return out


def _date_num(date: str) -> int:
    """'2019-04-11' or '11-04-2019' -> 20190411. 0 when unparseable, which
    sorts before everything and never satisfies a 'since' filter by accident."""
    if not date:
        return 0
    digits = re.findall(r"\d+", str(date))
    if len(digits) < 3:
        return 0
    a, b, c = digits[0], digits[1], digits[2]
    if len(a) == 4:
        y, m, d = a, b, c
    else:
        d, m, y = a, b, c
    try:
        return int(f"{int(y):04d}{int(m):02d}{int(d):02d}")
    except ValueError:
        return 0


# --- court level, used for jurisdiction filtering -------------------------

_HIGH_COURT_STATE = {
    "madhya pradesh": "IN-MP", "bombay": "IN-MH", "delhi": "IN-DL",
    "calcutta": "IN-WB", "madras": "IN-TN", "karnataka": "IN-KA",
    "allahabad": "IN-UP", "gujarat": "IN-GJ", "rajasthan": "IN-RJ",
    "kerala": "IN-KL", "patna": "IN-BR", "punjab": "IN-PB",
    "andhra": "IN-AP", "telangana": "IN-TG", "orissa": "IN-OR",
    "chhattisgarh": "IN-CT", "jharkhand": "IN-JH", "gauhati": "IN-AS",
    "himachal": "IN-HP", "uttarakhand": "IN-UK", "jammu": "IN-JK",
    "sikkim": "IN-SK", "manipur": "IN-MN", "meghalaya": "IN-ML",
    "tripura": "IN-TR",
}


def classify_court(docsource: str) -> Dict[str, str]:
    """Map Kanoon's `docsource` to a court level and a jurisdiction code."""
    s = (docsource or "").lower()
    if "supreme court" in s:
        return {"court_level": "supreme_court", "jurisdiction": "IN"}
    if "high court" in s:
        for name, code in _HIGH_COURT_STATE.items():
            if name in s:
                return {"court_level": "high_court", "jurisdiction": code}
        return {"court_level": "high_court", "jurisdiction": "IN"}
    if "tribunal" in s or "commission" in s:
        return {"court_level": "tribunal", "jurisdiction": "IN"}
    if s:
        return {"court_level": "other", "jurisdiction": "IN"}
    return {"court_level": "", "jurisdiction": "IN"}