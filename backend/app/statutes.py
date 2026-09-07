"""Bare-act retrieval over locally ingested statutes.

Kanoon gives us case law. This module gives us the statute text itself, so
"what is BNS 318" or "my husband is cruel to me" have a real provision to
ground against instead of returning tangential judgments.

Data lives in backend/data/*.json. Fetch it with:

    python -m app.ingest_bns          # Bharatiya Nyaya Sanhita (current law)
    python -m app.ingest_statutes     # IPC, CrPC, NI Act, etc. (optional)

Scoring is BM25 over section title + body. Pure Python, no extra deps -
358 sections is small enough that a linear scan is sub-millisecond.
"""

import json
import logging
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_K1 = 1.5
_B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "be", "shall",
    "for", "with", "by", "on", "as", "any", "such", "which", "that", "this",
    "he", "his", "him", "it", "not", "who", "whoever", "may", "can", "what",
    "how", "my", "me", "i", "do", "does", "did", "if", "was", "were", "been",
    "has", "have", "under", "section", "act", "code", "am", "about", "tell",
    "doing", "done", "there", "their", "her", "she", "they", "them", "you",
}

# Acts we know how to load.
#   current      - False means repealed; the synthesis agent is told so it
#                  never presents repealed law as live law.
#   url_template - per-section public page, so citations are clickable.
def _coi_url(section: str) -> str:
    """Per-article page. Zero-padded to three digits, suffix lowercased:
    "21" -> article_021, "21A" -> article_021a."""
    if section.lower() == "preamble":
        return "https://www.constitutionofindia.net/constitution_of_india/preamble/"
    m = re.match(r"(\d+)([A-Za-z]*)", section)
    if not m:
        return "https://www.constitutionofindia.net/articles/"
    num, suffix = m.groups()
    return f"http://constitutionofindia.etal.in/article_{int(num):03d}{suffix.lower()}/"


ACTS: Dict[str, Dict[str, Any]] = {
    "coi": {
        "file": "coi.json",
        "name": "Constitution of India, 1950",
        "short": "Constitution",
        "current": True,
        "superseded_by": None,
        "url_template": _coi_url,
        # Sorts below the penal code: constitutional articles are broad enough
        # that they'd otherwise crowd out the specific offence provisions.
        "priority": 3,
        "unit": "Article",
    },
    "bns": {
        "file": "bns.json",
        "name": "Bharatiya Nyaya Sanhita, 2023",
        "short": "BNS",
        "current": True,
        "superseded_by": None,
        "url_template": "https://devgan.in/bns/section/{section}/",
        "priority": 0,  # lower sorts first when scores tie
    },
    "ipc": {
        "file": "ipc.json",
        "name": "Indian Penal Code, 1860",
        "short": "IPC",
        "current": False,
        "superseded_by": "Bharatiya Nyaya Sanhita, 2023 (in force from 1 July 2024)",
        "url_template": "https://devgan.in/ipc/section/{section}/",
        "priority": 2,
        "unit": "Section",
    },
    "crpc": {
        "file": "crpc.json",
        "name": "Code of Criminal Procedure, 1973",
        "short": "CrPC",
        "current": False,
        "superseded_by": "Bharatiya Nagarik Suraksha Sanhita, 2023 (in force from 1 July 2024)",
        "url_template": "https://devgan.in/crpc/section/{section}/",
        "priority": 2,
        "unit": "Section",
    },
    "iea": {
        "file": "iea.json",
        "name": "Indian Evidence Act, 1872",
        "short": "IEA",
        "current": False,
        "superseded_by": "Bharatiya Sakshya Adhiniyam, 2023 (in force from 1 July 2024)",
        "url_template": None,
        "priority": 2,
        "unit": "Section",
    },
    "contract": {
        "file": "contract.json",
        "name": "Indian Contract Act, 1872",
        "short": "Contract Act",
        "current": True,
        "superseded_by": None,
        "url_template": "https://indiankanoon.org/search/?formInput=section+{section}+indian+contract+act",
        "priority": 1,
        "unit": "Section",
    },
    "tpa": {
        "file": "tpa.json",
        "name": "Transfer of Property Act, 1882",
        "short": "TP Act",
        "current": True,
        "superseded_by": None,
        "url_template": "https://indiankanoon.org/search/?formInput=section+{section}+transfer+of+property+act",
        "priority": 1,
        "unit": "Section",
    },
    "rti": {
        "file": "rti.json",
        "name": "Right to Information Act, 2005",
        "short": "RTI Act",
        "current": True,
        "superseded_by": None,
        "url_template": "https://indiankanoon.org/search/?formInput=section+{section}+right+to+information+act",
        "priority": 1,
        "unit": "Section",
    },
    "nia": {
        "file": "nia.json",
        "name": "Negotiable Instruments Act, 1881",
        "short": "NI Act",
        "current": True,
        "superseded_by": None,
        "url_template": None,
        "priority": 1,
        "unit": "Section",
    },
    "cpc": {
        "file": "cpc.json",
        "name": "Code of Civil Procedure, 1908",
        "short": "CPC",
        "current": True,
        "superseded_by": None,
        "url_template": None,
        "priority": 1,
        "unit": "Section",
    },
    "hma": {
        "file": "hma.json",
        "name": "Hindu Marriage Act, 1955",
        "short": "HMA",
        "current": True,
        "superseded_by": None,
        "url_template": None,
        "priority": 1,
        "unit": "Section",
    },
    "mva": {
        "file": "MVA.json",
        "name": "Motor Vehicles Act, 1988",
        "short": "MV Act",
        "current": True,
        "superseded_by": None,
        "url_template": None,
        "priority": 1,
        "unit": "Section",
    },
}

# Sections that travel together. When one is retrieved, pull the others in
# so the answer isn't half a rule - e.g. BNS 85 creates the cruelty offence
# but BNS 86 is where "cruelty" is actually defined.
COMPANIONS: Dict[str, List[str]] = {
    "bns:85": ["86"],
    "bns:86": ["85"],
    "bns:80": ["85", "86"],
    "bns:100": ["101", "103"],
    "bns:101": ["103"],
    "bns:115": ["117"],
    "bns:318": ["319"],
    "bns:63": ["64"],
    "bns:64": ["63"],
    "bns:303": ["305"],
}

# Statutes outside the ingested bare acts that a user should know about for
# certain topics. Purely additive context, surfaced as extra next steps.
RELATED_LAWS: Dict[str, List[Dict[str, str]]] = {
    "bns:85": [
        {
            "name": "Protection of Women from Domestic Violence Act, 2005",
            "why": (
                "A civil law that runs alongside the criminal provision. It covers "
                "physical, emotional, sexual and economic abuse, and lets a magistrate "
                "grant protection orders, residence orders (the right to stay in the "
                "shared household) and monetary relief - without needing a criminal case."
            ),
            "url": "https://www.indiacode.nic.in/handle/123456789/2021",
        },
    ],
}
RELATED_LAWS["bns:86"] = RELATED_LAWS["bns:85"]


# People don't describe their problems in statutory language. "Someone stole
# my phone" shares no words with "dishonestly takes movable property out of
# the possession of any person". This maps everyday phrasing onto the terms
# the bare act actually uses, so BM25 has something to match.
#
# Keys are matched against the raw lowercased query (substring match, so
# multi-word keys work); values are appended to the query's token list.
_CONCEPTS: Dict[str, List[str]] = {
    # violence in the home
    "domestic violence": ["cruelty", "husband", "woman", "harassment"],
    "beats me": ["cruelty", "hurt", "assault"],
    "beat me": ["cruelty", "hurt", "assault"],
    "hits me": ["cruelty", "hurt", "assault"],
    "in-laws": ["relative", "husband", "cruelty"],
    "in laws": ["relative", "husband", "cruelty"],
    "sasural": ["relative", "husband", "cruelty"],
    "mental torture": ["cruelty", "wilful", "conduct"],
    "harass": ["harassment", "cruelty"],
    "dowry": ["dowry", "unlawful", "demand", "property"],
    # property
    "stole": ["theft", "dishonestly", "movable", "property"],
    "stolen": ["theft", "dishonestly", "movable", "property"],
    "steal": ["theft", "dishonestly", "movable", "property"],
    "robbed": ["robbery", "theft", "extortion"],
    "snatched": ["theft", "robbery"],
    "cheated": ["cheating", "deceiving", "dishonestly", "fraudulently"],
    "fraud": ["cheating", "fraudulently", "deceiving"],
    "scam": ["cheating", "deceiving", "dishonestly"],
    "duped": ["cheating", "deceiving"],
    "forged": ["forgery", "false", "document"],
    "trespass": ["trespass", "possession", "property"],
    # person
    "threatened": ["criminal", "intimidation", "threatens", "injury", "alarm"],
    "threat": ["criminal", "intimidation", "threatens", "alarm"],
    "blackmail": ["extortion", "threat", "intimidation"],
    "molest": ["outrage", "modesty", "criminal", "force", "assault"],
    "touched me": ["outrage", "modesty", "criminal", "force", "assault"],
    "eve teasing": ["modesty", "insult", "harassment"],
    "stalking": ["stalking", "follows", "woman", "contact"],
    "followed me": ["stalking", "follows"],
    "obscene": ["obscene", "modesty", "indecent"],
    "kidnap": ["kidnapping", "abduction"],
    "abduct": ["abduction", "kidnapping"],
    "murder": ["murder", "culpable", "homicide", "death"],
    "killed": ["murder", "culpable", "homicide", "death"],
    "suicide": ["abetment", "suicide", "instigates"],
    "injured me": ["hurt", "grievous", "bodily", "pain"],
    "acid": ["acid", "grievous", "hurt", "permanent", "damage"],
    # reputation / speech
    "defam": ["defamation", "imputation", "reputation"],
    "slander": ["defamation", "imputation", "reputation"],
    "false case": ["false", "charge", "malicious", "proceeding"],
    "rumour": ["defamation", "imputation", "reputation"],
    # constitutional
    "fundamental right": ["fundamental", "rights", "state", "citizens"],
    "free speech": ["freedom", "speech", "expression", "citizens"],
    "freedom of speech": ["freedom", "speech", "expression"],
    "right to equality": ["equality", "law", "discriminate"],
    "discriminat": ["discriminate", "religion", "race", "caste", "sex"],
    "caste": ["caste", "discriminate", "untouchability"],
    "untouchab": ["untouchability", "abolished"],
    "arrested": ["arrest", "detained", "custody", "grounds", "practitioner"],
    "without a warrant": ["arrest", "detained", "custody"],
    "right to education": ["education", "free", "compulsory", "children"],
    "right to privacy": ["life", "personal", "liberty"],
    "religion": ["religion", "conscience", "profess", "practise", "propagate"],
    "writ": ["writs", "habeas", "corpus", "mandamus", "supreme", "court"],
    "habeas corpus": ["habeas", "corpus", "writs", "supreme", "court"],
    "amend the constitution": ["amendment", "constitution", "parliament"],
    # organised / cyber
    "gang": ["organised", "crime", "syndicate", "group"],
    "online fraud": ["cheating", "deceiving", "dishonestly"],
    "cyber": ["cheating", "deceiving", "electronic"],
    "ransom": ["kidnapping", "ransom", "abduction"],
}


def _expand(query: str) -> List[str]:
    """Tokenize a user query and bolt on statutory synonyms."""
    tokens = _tokenize(query)
    q = query.lower()
    for phrase, extra in _CONCEPTS.items():
        if phrase in q:
            tokens.extend(extra)
    return tokens


def _tokenize(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def _normalize(row: Dict[str, Any], act_key: str) -> Optional[Dict[str, Any]]:
    meta = ACTS[act_key]
    number = row.get("Section", row.get("section", row.get("section_number")))
    body = (row.get("section_desc") or row.get("Legal Definition") or row.get("definition") or "").strip()
    if number is None or not body:
        return None

    title = (row.get("section_title") or row.get("Title") or row.get("title") or "").strip()
    section = str(number).strip()

    url = None
    tpl = meta["url_template"]
    if callable(tpl):
        url = tpl(section)
    elif tpl:
        url = tpl.format(section=section)

    return {
        "act": meta["name"],
        "act_short": meta["short"],
        "act_key": act_key,
        "current": meta["current"],
        "superseded_by": meta["superseded_by"],
        "priority": meta["priority"],
        "unit": meta.get("unit", "Section"),
        "section": section,
        "chapter": row.get("chapter_title") or "",
        "title": title,
        "text": body,
        "url": url,
        # Context-aware chunk. The chapter heading is what lets "fundamental
        # rights" find Article 21, which contains neither word. The act name
        # adds almost nothing on its own - it appears in every section of the
        # act, so BM25's IDF discards it - but it costs nothing and helps
        # cross-act queries. Title is weighted 4x: measured best on the eval.
        "_tokens": _tokenize(
            f"{meta['short']} {row.get('chapter_title') or ''} "
            f"{title} {title} {title} {title} {body}"
        ),
    }


@lru_cache(maxsize=1)
def _index() -> Dict[str, Any]:
    docs: List[Dict[str, Any]] = []

    for key, meta in ACTS.items():
        path = DATA_DIR / meta["file"]
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read %s: %s", path, exc)
            continue

        rows = raw if isinstance(raw, list) else raw.get("sections", [])
        for row in rows:
            doc = _normalize(row, key)
            if doc:
                docs.append(doc)

    if not docs:
        logger.warning("No statute data in %s. Run: python -m app.ingest_bns", DATA_DIR)
        return {"docs": [], "by_key": {}, "df": {}, "avg_len": 0.0}

    df: Counter = Counter()
    for d in docs:
        df.update(set(d["_tokens"]))

    by_key = {f"{d['act_key']}:{d['section']}": d for d in docs}
    avg_len = sum(len(d["_tokens"]) for d in docs) / len(docs)
    logger.info("Loaded %d statute sections from %s", len(docs), DATA_DIR)
    return {"docs": docs, "by_key": by_key, "df": df, "avg_len": avg_len}


def available_acts() -> List[str]:
    return sorted({d["act"] for d in _index()["docs"]})


def get(act_key: str, section: str) -> Optional[Dict[str, Any]]:
    return _index()["by_key"].get(f"{act_key}:{section}")


# --- act-level overview ---------------------------------------------------
# BM25 can't answer "tell me about the BNS" - every token is a stopword or
# the act name itself. Handle those explicitly.

_ACT_OVERVIEW_RE = re.compile(
    r"\b(what|tell|explain|about|overview|meaning|introduce|describe)\b.{0,40}?"
    r"\b(bns|bharatiya nyaya sanhita|nyaya sanhita|ipc|indian penal code|"
    r"crpc|bnss|nagarik suraksha|coi|constitution of india|indian constitution|"
    r"constitution)\b",
    re.IGNORECASE | re.DOTALL,
)

_OVERVIEWS: Dict[str, Dict[str, str]] = {
    "coi": {
        "title": "Constitution of India, 1950",
        "text": (
            "The Constitution of India is the country's supreme law. It was adopted "
            "by the Constituent Assembly on 26 November 1949 and came into force on "
            "26 January 1950. Any law that conflicts with it is void to the extent "
            "of that conflict.\n\n"
            "It is organised into Parts. Part III sets out the Fundamental Rights "
            "(Articles 12 to 35) - equality before the law, freedom of speech, "
            "protection of life and personal liberty, protection against arbitrary "
            "arrest, and the right to move the Supreme Court directly to enforce "
            "these rights. Part IV contains the Directive Principles, which guide "
            "the State but cannot be enforced in court. Part IVA lists the "
            "Fundamental Duties of citizens.\n\n"
            "The remaining Parts set up the Union and State governments, the "
            "judiciary, elections, the division of powers between the Centre and "
            "the States, emergency provisions, and the procedure for amending the "
            "Constitution itself.\n\n"
            "The Constitution opens with the Preamble, which declares India a "
            "sovereign, socialist, secular, democratic republic and states the "
            "goals of justice, liberty, equality and fraternity."
        ),
        "url": "https://www.constitutionofindia.net/articles/",
    },
    "bns": {
        "title": "Bharatiya Nyaya Sanhita, 2023",
        "text": (
            "The Bharatiya Nyaya Sanhita, 2023 (Act 45 of 2023) is India's criminal "
            "code. It came into force on 1 July 2024 and replaced the Indian Penal "
            "Code, 1860. It has 358 sections across 20 chapters, covering offences "
            "against the body, against women and children, against property, against "
            "the State, and offences affecting public health, safety and decency, "
            "along with general rules on punishment and the general exceptions "
            "(including the right of private defence).\n\n"
            "It is one of three new criminal laws that took effect the same day: the "
            "Bharatiya Nagarik Suraksha Sanhita, 2023 replaced the Code of Criminal "
            "Procedure, 1973, and the Bharatiya Sakshya Adhiniyam, 2023 replaced the "
            "Indian Evidence Act, 1872.\n\n"
            "Notable changes from the IPC include new offences for organised crime "
            "(section 111), petty organised crime (section 112) and terrorist acts "
            "(section 113); community service as a punishment; and the replacement of "
            "the old sedition provision with an offence covering acts that endanger "
            "the sovereignty, unity and integrity of India (section 152). Section "
            "numbers changed throughout, so an offence people still call by its old "
            "IPC number now sits elsewhere - cheating moved from IPC 420 to BNS 318, "
            "and murder from IPC 302 to BNS 103."
        ),
        "url": "https://devgan.in/all_sections_bns.php",
    },
    "ipc": {
        "title": "Indian Penal Code, 1860",
        "text": (
            "The Indian Penal Code, 1860 was India's criminal code for over 160 years. "
            "It was repealed with effect from 1 July 2024 and replaced by the "
            "Bharatiya Nyaya Sanhita, 2023.\n\n"
            "It still matters: offences committed before 1 July 2024 continue to be "
            "investigated and tried under the IPC, and almost all reported case law "
            "cites IPC section numbers. But it is not the law that applies to "
            "anything happening now."
        ),
        "url": "https://devgan.in/all_sections_ipc.php",
    },
}

_ACT_ALIASES = {
    "constitution": "coi",
    "contract act": "contract",
    "indian contract act": "contract",
    "transfer of property act": "tpa",
    "transfer of property": "tpa",
    "tp act": "tpa",
    "tpa": "tpa",
    "ni act": "nia",
    "rti act": "rti",
    "rti": "rti",
    "right to information act": "rti",
    "consumer protection act": "cpa",
    "cpa": "cpa",
    "specific relief act": "specific_relief",
    "limitation act": "limitation",
    "arbitration act": "arbitration",
    "arbitration and conciliation act": "arbitration",
    "it act": "ita",
    "information technology act": "ita",
    "evidence act": "iea",
    "indian evidence act": "iea",
    "bsa": "iea",
    "civil procedure code": "cpc",
    "code of civil procedure": "cpc",
    "criminal procedure code": "crpc",
    "code of criminal procedure": "crpc",
    "hindu marriage act": "hma",
    "motor vehicles act": "mva",
    "constitution of india": "coi",
    "indian constitution": "coi",
    "bharatiya nyaya sanhita": "bns",
    "nyaya sanhita": "bns",
    "indian penal code": "ipc",
    "penal code": "ipc",
    "negotiable instrument act": "nia",
    "negotiable instruments act": "nia",
    "bnss": "crpc",
    "nagarik suraksha": "crpc",
}


def act_overview(query: str) -> Optional[Dict[str, str]]:
    """Return an act-level summary when the user asks about a whole act.

    Defers to section lookup when the query names a number - "what is BNS 85"
    is a section question, not a request for an overview of the whole code.
    """
    if re.search(r"\d", query):
        return None
    m = _ACT_OVERVIEW_RE.search(query)
    if not m:
        return None
    raw = m.group(2).lower()
    key = _ACT_ALIASES.get(raw, raw)
    return _OVERVIEWS.get(key)


# --- direct citation lookup, e.g. "BNS 85", "section 318 of BNS" ----------

# Short act tokens that can appear on either side of the number.
_ACT_TOKENS = (
    r"bns|bnss|bsa|ipc|crpc|cpc|iea|nia|hma|mva|coi|cpa|rti|tpa|ita"
)

# Longer act names, only ever written after the number ("section 138 of the
# Negotiable Instruments Act"). Ordered longest-first so "indian contract act"
# wins over "contract act".
_ACT_NAMES = (
    r"bharatiya nyaya sanhita|nyaya sanhita|"
    r"bharatiya nagarik suraksha sanhita|nagarik suraksha sanhita|"
    r"bharatiya sakshya adhiniyam|sakshya adhiniyam|"
    r"constitution of india|indian constitution|constitution|"
    r"indian penal code|penal code|"
    r"code of criminal procedure|criminal procedure code|"
    r"code of civil procedure|civil procedure code|"
    r"indian evidence act|evidence act|"
    r"negotiable instruments? act|ni act|"
    r"indian contract act|contract act|"
    r"transfer of property act|transfer of property|tp act|"
    r"right to information act|rti act|"
    r"consumer protection act|"
    r"specific relief act|limitation act|"
    r"arbitration and conciliation act|arbitration act|"
    r"information technology act|it act|"
    r"hindu marriage act|motor vehicles act"
)

_CITE_RE = re.compile(
    rf"(?:(?P<act1>{_ACT_TOKENS})\s*)?"
    r"(?P<unit>article|art\.?|section|sec\.?|s\.?|u/s)?\s*"
    r"(?P<num>\d{1,3}[a-z]{0,2})"
    rf"(?:\s*(?:of\s+(?:the\s+)?)?(?P<act2>{_ACT_TOKENS}|{_ACT_NAMES}))?",
    re.IGNORECASE,
)

# Reported case citations - "AIR 1973 SC 1461", "(2017) 10 SCC 1",
# "2019 SCC OnLine SC 1005". Not looked up locally (we have no judgment
# corpus), but recognising them means the query can be routed straight to
# Kanoon instead of being tokenised into meaningless numbers.
_CASE_CITE_RE = re.compile(
    # Lookbehind, not \b: a citation can open with "(", and \b requires a
    # word character to sit against, so "\b(2017) 10 SCC 1" never matched.
    r"(?<![A-Za-z0-9])(?:AIR\s+\d{4}\s+[A-Z]{2,4}\s+\d+"
    r"|\(\d{4}\)\s*\d+\s*SCC\s*(?:OnLine\s*[A-Z]{2,4}\s*)?\d+"
    r"|\d{4}\s+SCC\s+OnLine\s+[A-Z]{2,4}\s+\d+"
    r"|\(\d{4}\)\s*\d+\s*[A-Z]{2,5}\s*\d+)\b",
    re.IGNORECASE,
)


def case_citations(query: str) -> List[str]:
    """Reported citations named in the query, e.g. ['AIR 1973 SC 1461'].

    A query naming a specific judgment should go to case-law search, not to
    BM25 over statute text - the numbers in a citation are noise there.
    """
    return [m.group(0).strip() for m in _CASE_CITE_RE.finditer(query or "")]

# "give me the preamble of the constitution"
_PREAMBLE_RE = re.compile(r"\bpreamble\b", re.IGNORECASE)


def lookup_section(query: str) -> List[Dict[str, Any]]:
    """Pull exact sections when the user cites one directly."""
    docs = _index()["docs"]
    if not docs:
        return []

    hits: List[Dict[str, Any]] = []
    seen = set()

    for m in _CITE_RE.finditer(query):
        num = m.group("num")
        if not num:
            continue
        raw_act = (m.group("act1") or m.group("act2") or "").lower().strip()
        act = _ACT_ALIASES.get(raw_act, raw_act)

        # "Article 21" means the Constitution even when no act is named -
        # no other ingested law numbers its provisions as articles.
        unit = (m.group("unit") or "").lower().rstrip(".")
        if not act and unit in ("article", "art"):
            act = "coi"

        matches = [
            d for d in docs
            if d["section"].lower() == num.lower() and (not act or d["act_key"] == act)
        ]
        # No act named -> prefer current law (BNS) over repealed codes.
        matches.sort(key=lambda d: d["priority"])

        for d in matches:
            key = (d["act_key"], d["section"])
            if key not in seen:
                seen.add(key)
                hits.append(d)

    return hits[:5]


# --- BM25 keyword search --------------------------------------------------

# Ranking weights. Exposed as module state so eval_retrieval --sweep can tune
# them against the question set instead of anyone guessing.
#
# PRIORITY_STEP  - how much each step down the ACTS priority ladder costs.
#                  Constitutional articles are broad and would otherwise crowd
#                  out the specific provision that actually governs.
# REPEALED_MULT  - what a repealed act's sections are multiplied by. This is a
#                  legal judgement, not a tuning knob: the CrPC was repealed in
#                  2024, so a CrPC section should almost never outrank a live
#                  BNS one. It is still retrievable - someone asking about an
#                  old case needs it - just not first.
# Measured on the question set with `--sweep`, not chosen by intuition.
# PRIORITY_STEP at 0 was strictly better: the priority ladder was demoting
# constitutional articles that were the correct answer. The repeal penalty
# does earn its keep - repealed CrPC sections were outranking live BNS ones.
PRIORITY_STEP = 0.0
REPEALED_MULT = 0.7


def _rank_weight(doc: Dict[str, Any]) -> float:
    w = 1.0 - PRIORITY_STEP * doc.get("priority", 0)
    if not doc.get("current", True):
        w *= REPEALED_MULT
    return max(w, 0.05)


def _bm25(query: str, docs: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    idx = _index()
    terms = _expand(query)
    if not terms:
        return []

    n = len(docs)
    df = idx["df"]
    avg_len = idx["avg_len"] or 1.0

    idf = {
        t: math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
        for t in set(terms)
    }

    scored = []
    for d in docs:
        tf = Counter(d["_tokens"])
        dl = len(d["_tokens"]) or 1
        score = 0.0
        for t in terms:
            f = tf.get(t, 0)
            if not f:
                continue
            score += idf[t] * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * dl / avg_len))
        if score > 0:
            score *= _rank_weight(d)
            scored.append((score, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:limit]]


def _with_companions(hits: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Pull in sections that are meaningless on their own (85 needs 86)."""
    out = list(hits)
    seen = {f"{d['act_key']}:{d['section']}" for d in out}

    for d in hits:
        for comp in COMPANIONS.get(f"{d['act_key']}:{d['section']}", []):
            key = f"{d['act_key']}:{comp}"
            if key in seen:
                continue
            doc = _index()["by_key"].get(key)
            if doc:
                out.append(doc)
                seen.add(key)

    return out[: limit + 2]  # allow a little headroom for companions


def search(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Return the statute sections most relevant to a plain-language query."""
    docs = _index()["docs"]
    if not docs:
        return []

    # 1. The Preamble is asked for by name, not by number.
    if _PREAMBLE_RE.search(query):
        pre = _index()["by_key"].get("coi:Preamble")
        if pre:
            return [pre]

    # 2. An explicit citation beats fuzzy scoring every time.
    exact = lookup_section(query)
    if exact:
        return _with_companions(exact[:limit], limit)

    # 3. Otherwise BM25.
    return _with_companions(_bm25(query, docs, limit), limit)


def related_laws(hits: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Statutes outside the ingested acts that matter for these sections."""
    out: List[Dict[str, str]] = []
    seen = set()
    for d in hits:
        for law in RELATED_LAWS.get(f"{d['act_key']}:{d['section']}", []):
            if law["name"] not in seen:
                seen.add(law["name"])
                out.append(law)
    return out


# --- shape statute hits like Kanoon hits so the pipeline can merge them ----

def as_source(doc: Dict[str, Any]) -> Dict[str, Any]:
    status = ""
    if not doc["current"] and doc["superseded_by"]:
        status = (
            f"\n\nIMPORTANT - THIS PROVISION IS REPEALED. It has been replaced by "
            f"{doc['superseded_by']}. It still governs offences committed before "
            f"that date, but it is not the law that applies to new matters."
        )

    unit = doc.get("unit", "Section")
    if doc["section"].lower() == "preamble":
        label = doc["title"] or "Preamble"
    else:
        label = (
            f"{unit} {doc['section']} — {doc['title']}"
            if doc["title"] else f"{unit} {doc['section']}"
        )

    return {
        "docid": None,
        # Identity fields have to survive the conversion. Without them the
        # validity layer can't tell a statute from a judgment, so every
        # statute-backed answer was being reported as ungrounded and no
        # repeal badge could ever be computed.
        "act_key": doc["act_key"],
        "act_short": doc["act_short"],
        "act": doc.get("act"),
        "section": doc["section"],
        "current": doc["current"],
        "superseded_by": doc.get("superseded_by"),
        "kind": "section",
        "title": f"{doc['act_short']} {label}",
        "court": "Bare Act" + ("" if doc["current"] else " (repealed)"),
        "date": None,
        "snippet": doc["text"][:300],
        "text": doc["text"] + status,
        "url": doc["url"] or (
            "https://indiankanoon.org/search/?formInput="
            + f"{doc['act_short']} section {doc['section']}".replace(" ", "+")
        ),
    }


def overview_as_source(ov: Dict[str, str]) -> Dict[str, Any]:
    return {
        "docid": None,
        # Marks this as statute material rather than case law. The grounding
        # assessment counts bare-act sources, and without this an act overview
        # looked like an answer with no statutory basis at all.
        "kind": "overview",
        "title": ov["title"],
        "court": "Bare Act — overview",
        "date": None,
        "snippet": ov["text"][:300],
        "text": ov["text"],
        "url": ov["url"],
    }