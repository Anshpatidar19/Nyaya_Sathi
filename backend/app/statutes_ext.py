"""Curated corpus extension sourced from the `mratanusarkar/Indian-Laws`
Hugging Face dataset.

Why this is a separate module and not more entries in statutes.ACTS:

statutes.py is the retrieval engine. Every act added there is a permanent
commitment - a BM25 document set, a citation regex alternative, an alias, an
overview. Bolting twenty more acts inline would have made that file
unreviewable and would have mixed two very different kinds of claim: acts
whose text was hand-checked against the bare act, and acts whose text came
out of a third-party dataset scrape.

So the extension lives here, gets merged into statutes.ACTS at import time,
and every entry carries provenance (`source`, `source_url`) plus an explicit
act-level `status`. statutes.py stays the engine; this file is the data.

The selection principle is *missing + current + reliable primary law*, not
vector count. The dataset has ~34k rows; the allowlist below is a few
thousand sections. Everything not named here is excluded by default, which
is the safe direction for a legal corpus - an act we never ingested cannot
be cited wrongly.

Act-level status vocabulary (mirrors validity.ACT_STATUS_*):

    live       in force, consolidated text, safe to present as current law
    repealed   no longer in force; needs a successor pointer
    amending   an amendment act - modifies another act, never stands alone.
               NEVER ingested as a standalone law.
    successor  in force, and it is what replaced a repealed act. Same
               practical effect as `live`; the distinction exists so the
               validity layer can say "CrPC 154 is now BNSS 173".

Adding an act:
  1. add an entry below with hf_match / hf_reject
  2. run `python -m app.ingest_hf_acts --dry-run` and read the report
  3. only then run with --write-local --vectors
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# --- provenance -----------------------------------------------------------

HF_DATASET = "mratanusarkar/Indian-Laws"
HF_URL = "https://huggingface.co/datasets/mratanusarkar/Indian-Laws"

# Tag written onto every vector produced from this extension. It is the
# rollback handle: `--rollback` deletes by this filter, so a bad ingest can be
# undone without touching the hand-checked original corpus.
CORPUS_BATCH = "hf-indian-laws-v1"


def _kanoon(words: str):
    """Per-section public search URL, so citations stay clickable.

    India Code has no stable per-section URL - it paginates a PDF - so a
    Kanoon search scoped to the act name is the most reliable clickable
    target. Same approach the existing ACTS entries use.
    """
    q = words.replace(" ", "+")
    return "https://indiankanoon.org/search/?formInput=section+{section}+" + q


# --- what counts as an amendment act, never a law in its own right --------
#
# This is the single most important filter in the file. The dataset contains
# hundreds of rows whose act_title is an amendment act. Ingesting one as a
# standalone law produces answers like "under the SC/ST (Amendment) Act,
# 2015, ..." quoting a fragment that only makes sense as a diff against the
# 1989 act. Rejected before anything else runs.
AMENDING_RE = re.compile(
    r"\b(?:"
    r"amendment|amending|"
    r"repealing\s+and\s+amending|"
    r"adaptation\s+of\s+laws|"
    r"removal\s+of\s+difficulties|"
    r"validating|validation|"
    r"miscellaneous\s+provisions\s+act,\s*(?:19|20)\d\d\s*\(.*amend"
    r")\b",
    re.IGNORECASE,
)

# Rows that are not primary legislation at all: rules, regulations, schemes,
# notifications, orders, bye-laws. The dataset mixes them in. They are not
# wrong, they are just not what a citation-traceable statute corpus should
# present as "the Act says".
NON_PRIMARY_RE = re.compile(
    r"\b(?:rules?|regulations?|scheme|notification|order|bye[\s-]?laws?|"
    r"ordinance|bill|manual|guidelines?|circular)\b",
    re.IGNORECASE,
)

# ...but the words above appear inside plenty of genuine Act titles, and the
# first version of this filter dropped 1,333 rows of real primary legislation
# because of it: "Banking Regulation Act, 1949", "Real Estate (Regulation and
# Development) Act, 2016", "Foreign Contribution (Regulation) Act, 2010" are
# Acts of Parliament, not subordinate instruments.
#
# The discriminator is the INSTRUMENT WORD - the noun the title ends on. An
# Act ends "... Act, 1949". A rule ends "... Rules, 2020". So a title whose
# terminal instrument word is primary is kept regardless of what appears
# earlier in it, and NON_PRIMARY_RE only decides the cases where it is not.
#
# This mattered less than it looks today - none of the affected titles are on
# the allowlist - but NON_PRIMARY_RE runs BEFORE allowlist matching, so
# without this an act added to the allowlist later would be dropped silently
# with no way to tell from the report why.
PRIMARY_INSTRUMENT_RE = re.compile(
    r"\b(?:act|sanhita|adhiniyam|samhita|code|constitution)\b"
    r"(?:\s*(?:no\s*\d+\s*)?(?:of\s*)?(?:1[6-9]|20)\d\d)?\s*$",
    re.IGNORECASE,
)


def normalise_title(title: str) -> str:
    """Fold an act_title to a comparable key.

    The dataset's titles are inconsistent about "The", commas before the
    year, ampersands, and bracketed short names. Normalising before matching
    stops "SC and ST (Prevention of Atrocities) Act 1989" and "The
    Scheduled Castes and the Scheduled Tribes (Prevention of Atrocities)
    Act, 1989" from being treated as two different acts.
    """
    t = (title or "").strip().lower()
    t = re.sub(r"^the\s+", "", t)
    t = t.replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# --- the allowlist --------------------------------------------------------
#
# tier:
#   priority  the eight acts explicitly asked for. Ingest first, validate,
#             then move on.
#   core      high-frequency live law that a general Indian legal assistant
#             is asked about constantly and currently has no answer for.
#   optional  useful but large or narrow. Off unless --tier optional.
#
# hf_match   regex against the NORMALISED act_title. Must match.
# hf_reject  optional extra regex; if it matches, the row is dropped even
#            when hf_match matched. Used where an act name is a substring of
#            a different act's name.

EXT_ACTS: Dict[str, Dict[str, Any]] = {

    # ================= tier: priority =================================

    "pocso": {
        "file": "pocso.json",
        "name": "Protection of Children from Sexual Offences Act, 2012",
        "short": "POCSO",
        "year": 2012,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("protection of children from sexual offences act"),
        "priority": 1,
        "unit": "Section",
        "tier": "priority",
        "hf_match": re.compile(r"protection of children from sexual offences act"),
    },
    "pwdva": {
        "file": "pwdva.json",
        "name": "Protection of Women from Domestic Violence Act, 2005",
        "short": "DV Act",
        "year": 2005,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("protection of women from domestic violence act"),
        "priority": 1,
        "unit": "Section",
        "tier": "priority",
        "hf_match": re.compile(r"protection of women from domestic violence act"),
    },
    "dowry": {
        "file": "dowry.json",
        "name": "Dowry Prohibition Act, 1961",
        "short": "Dowry Prohibition Act",
        "year": 1961,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("dowry prohibition act"),
        "priority": 1,
        "unit": "Section",
        "tier": "priority",
        "hf_match": re.compile(r"dowry prohibition act"),
    },
    "scst": {
        "file": "scst.json",
        "name": ("Scheduled Castes and the Scheduled Tribes "
                 "(Prevention of Atrocities) Act, 1989"),
        "short": "SC/ST Act",
        "year": 1989,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon(
            "scheduled castes and scheduled tribes prevention of atrocities act"),
        "priority": 1,
        "unit": "Section",
        "tier": "priority",
        # "prevention of atrocities" is the discriminating phrase. Without it
        # this would also swallow the SC/ST Orders (Amendment) Acts and the
        # various reservation-in-services acts.
        "hf_match": re.compile(
            r"scheduled castes?.*scheduled tribes?.*prevention of atrocities"),
    },
    "hsa": {
        "file": "hsa.json",
        "name": "Hindu Succession Act, 1956",
        "short": "HSA",
        "year": 1956,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("hindu succession act"),
        "priority": 1,
        "unit": "Section",
        "tier": "priority",
        "hf_match": re.compile(r"hindu succession act"),
    },
    "isa": {
        "file": "isa.json",
        "name": "Indian Succession Act, 1925",
        "short": "Indian Succession Act",
        "year": 1925,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("indian succession act"),
        "priority": 1,
        "unit": "Section",
        "tier": "priority",
        # Must not match "hindu succession act" - hence the anchor.
        "hf_match": re.compile(r"^indian succession act"),
        "hf_reject": re.compile(r"hindu"),
    },
    "bnss": {
        "file": "bnss.json",
        "name": "Bharatiya Nagarik Suraksha Sanhita, 2023",
        "short": "BNSS",
        "year": 2023,
        "current": True,
        # Not just live: it is specifically what replaced the CrPC, and the
        # validity layer uses that to answer "where did CrPC 154 go".
        "status": "successor",
        "supersedes": "crpc",
        "superseded_by": None,
        "url_template": "https://devgan.in/bnss/section/{section}/",
        "priority": 0,
        "unit": "Section",
        "tier": "priority",
        "hf_match": re.compile(
            r"bharatiya nagarik suraksha sanhita|nagarik suraksha sanhita"),
    },
    "bsa": {
        "file": "bsa.json",
        "name": "Bharatiya Sakshya Adhiniyam, 2023",
        "short": "BSA",
        "year": 2023,
        "current": True,
        "status": "successor",
        "supersedes": "iea",
        "superseded_by": None,
        "url_template": _kanoon("bharatiya sakshya adhiniyam"),
        "priority": 0,
        "unit": "Section",
        "tier": "priority",
        "hf_match": re.compile(
            r"bharatiya sakshya adhiniyam|sakshya adhiniyam"),
    },

    # ================= tier: core =====================================

    "jj": {
        "file": "jj.json",
        "name": "Juvenile Justice (Care and Protection of Children) Act, 2015",
        "short": "JJ Act",
        "year": 2015,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("juvenile justice care and protection of children act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        # 2015 consolidated and repealed the 2000 act. Reject the old one so
        # both versions never sit in the index at once.
        "hf_match": re.compile(r"juvenile justice.*care and protection of children"),
        "hf_reject": re.compile(r"\b2000\b|\b1986\b"),
    },
    "posh": {
        "file": "posh.json",
        "name": ("Sexual Harassment of Women at Workplace "
                 "(Prevention, Prohibition and Redressal) Act, 2013"),
        "short": "POSH Act",
        "year": 2013,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("sexual harassment of women at workplace act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"sexual harassment of women at workplace"),
    },
    "hama": {
        "file": "hama.json",
        "name": "Hindu Adoptions and Maintenance Act, 1956",
        "short": "HAMA",
        "year": 1956,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("hindu adoptions and maintenance act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"hindu adoptions? and maintenance act"),
    },
    "hmga": {
        "file": "hmga.json",
        "name": "Hindu Minority and Guardianship Act, 1956",
        "short": "HMGA",
        "year": 1956,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("hindu minority and guardianship act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"hindu minority and guardianship act"),
    },
    "sma": {
        "file": "sma.json",
        "name": "Special Marriage Act, 1954",
        "short": "Special Marriage Act",
        "year": 1954,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("special marriage act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"^special marriage act"),
    },
    "senior_citizens": {
        "file": "senior_citizens.json",
        "name": "Maintenance and Welfare of Parents and Senior Citizens Act, 2007",
        "short": "Senior Citizens Act",
        "year": 2007,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon(
            "maintenance and welfare of parents and senior citizens act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(
            r"maintenance and welfare of parents and senior citizens"),
    },
    "guardians_wards": {
        "file": "guardians_wards.json",
        "name": "Guardians and Wards Act, 1890",
        "short": "Guardians and Wards Act",
        "year": 1890,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("guardians and wards act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"guardians and wards act"),
    },
    "ndps": {
        "file": "ndps.json",
        "name": "Narcotic Drugs and Psychotropic Substances Act, 1985",
        "short": "NDPS Act",
        "year": 1985,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("narcotic drugs and psychotropic substances act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"narcotic drugs and psychotropic substances act"),
    },
    "pca": {
        "file": "pca.json",
        "name": "Prevention of Corruption Act, 1988",
        "short": "PC Act",
        "year": 1988,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("prevention of corruption act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"prevention of corruption act"),
        "hf_reject": re.compile(r"\b1947\b"),
    },
    "registration": {
        "file": "registration.json",
        "name": "Registration Act, 1908",
        "short": "Registration Act",
        "year": 1908,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("registration act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        # Anchored: dozens of dataset titles end in "Registration Act"
        # (Births and Deaths, Societies, Newspapers, Trade Marks...).
        "hf_match": re.compile(r"^registration act"),
    },
    "stamp": {
        "file": "stamp.json",
        "name": "Indian Stamp Act, 1899",
        "short": "Stamp Act",
        "year": 1899,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("indian stamp act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"^indian stamp act"),
    },
    "rpwd": {
        "file": "rpwd.json",
        "name": "Rights of Persons with Disabilities Act, 2016",
        "short": "RPwD Act",
        "year": 2016,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("rights of persons with disabilities act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"rights of persons with disabilities act"),
    },
    "phra": {
        "file": "phra.json",
        "name": "Protection of Human Rights Act, 1993",
        "short": "PHR Act",
        "year": 1993,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("protection of human rights act"),
        "priority": 1,
        "unit": "Section",
        "tier": "core",
        "hf_match": re.compile(r"protection of human rights act"),
    },

    # ================= tier: optional =================================

    "partnership": {
        "file": "partnership.json",
        "name": "Indian Partnership Act, 1932",
        "short": "Partnership Act",
        "year": 1932,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("indian partnership act"),
        "priority": 1,
        "unit": "Section",
        "tier": "optional",
        "hf_match": re.compile(r"^indian partnership act"),
    },
    "easements": {
        "file": "easements.json",
        "name": "Indian Easements Act, 1882",
        "short": "Easements Act",
        "year": 1882,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("indian easements act"),
        "priority": 1,
        "unit": "Section",
        "tier": "optional",
        "hf_match": re.compile(r"^indian easements act"),
    },
    "companies": {
        "file": "companies.json",
        "name": "Companies Act, 2013",
        "short": "Companies Act",
        "year": 2013,
        "current": True,
        "status": "live",
        "superseded_by": None,
        "url_template": _kanoon("companies act 2013"),
        "priority": 1,
        "unit": "Section",
        "tier": "optional",
        # ~470 sections, many of them long. Real value, but it roughly
        # doubles the vector count on its own, so it is opt-in.
        "hf_match": re.compile(r"^companies act,? 2013|^companies act 2013"),
        "hf_reject": re.compile(r"\b1956\b"),
    },
}

TIERS = ("priority", "core", "optional")


def acts_for_tiers(tiers: List[str]) -> Dict[str, Dict[str, Any]]:
    """Allowlist subset. `--tier priority` on the first run, widen after."""
    want = set(tiers)
    return {k: v for k, v in EXT_ACTS.items() if v.get("tier") in want}


def match_act_key(act_title: str, allow: Dict[str, Dict[str, Any]]):
    """Which allowlisted act does this dataset row belong to?

    Returns (act_key, None) on a hit, or (None, reason) on a miss. The reason
    string is what the dry-run report groups by, so it has to be specific
    enough to be actionable.
    """
    norm = normalise_title(act_title)
    if not norm:
        return None, "empty act_title"
    if AMENDING_RE.search(norm):
        return None, "amendment/amending act"
    # Instrument word decides. Only fall through to the keyword filter when
    # the title does not end on a primary-legislation noun - see the note on
    # PRIMARY_INSTRUMENT_RE.
    if not PRIMARY_INSTRUMENT_RE.search(norm) and NON_PRIMARY_RE.search(norm):
        return None, "not primary legislation (rules/regs/order)"

    for key, meta in allow.items():
        if not meta["hf_match"].search(norm):
            continue
        reject = meta.get("hf_reject")
        if reject and reject.search(norm):
            return None, f"matched {key} but hit hf_reject"
        return key, None
    return None, "not on allowlist"


# --- retrieval concepts ---------------------------------------------------
#
# Merged into statutes._CONCEPTS. Same contract: key is substring-matched
# against the lowercased query, values are appended to the BM25 token list.
#
# Two jobs here. First, acronyms and official names, so "POCSO" reaches an
# act whose sections never contain the word. Second - and this is the one
# that actually moves recall - the colloquial phrasing people really use.
# Nobody types "aggravated penetrative sexual assault".

EXT_CONCEPTS: Dict[str, List[str]] = {
    # POCSO
    "pocso": ["child", "children", "sexual", "assault", "harassment",
              "pornography", "minor", "special", "court"],
    "protection of children from sexual offences": [
        "child", "sexual", "assault", "penetrative", "aggravated"],
    "child sexual": ["child", "sexual", "assault", "penetrative", "minor"],
    "child abuse": ["child", "sexual", "assault", "harassment", "minor"],
    "minor girl": ["child", "minor", "age", "eighteen", "sexual"],
    "child pornography": ["pornographic", "child", "material", "sexual"],
    "child marriage": ["child", "marriage", "minor", "age", "prohibition"],

    # Domestic Violence Act (civil track, distinct from BNS cruelty)
    "domestic violence act": ["domestic", "violence", "aggrieved", "woman",
                              "shared", "household", "protection", "order"],
    "dv act": ["domestic", "violence", "aggrieved", "protection", "order"],
    "protection order": ["protection", "order", "magistrate", "respondent"],
    "residence order": ["residence", "shared", "household", "aggrieved"],
    "monetary relief": ["monetary", "relief", "maintenance", "aggrieved"],
    "thrown out of house": ["residence", "shared", "household", "dispossess"],
    "kicked me out": ["residence", "shared", "household", "dispossess"],
    "economic abuse": ["economic", "abuse", "financial", "resources"],
    "shared household": ["shared", "household", "residence", "aggrieved"],
    "protection officer": ["protection", "officer", "incident", "report"],

    # Dowry
    "dowry prohibition": ["dowry", "demand", "giving", "taking", "penalty"],
    "dowry demand": ["dowry", "demand", "penalty", "agreement"],
    "dowry death": ["dowry", "death", "cruelty", "seven", "years"],
    "gave dowry": ["dowry", "giving", "taking", "abetment"],

    # SC/ST
    "sc st": ["scheduled", "caste", "tribe", "atrocity", "atrocities"],
    "sc/st": ["scheduled", "caste", "tribe", "atrocity", "atrocities"],
    "scst": ["scheduled", "caste", "tribe", "atrocity"],
    "atrocities act": ["atrocities", "scheduled", "caste", "tribe", "special"],
    "atrocity": ["atrocities", "scheduled", "caste", "tribe"],
    "caste abuse": ["caste", "atrocities", "insult", "intimidation", "humiliation"],
    "casteist slur": ["caste", "insult", "intimidation", "public", "view"],
    "scheduled caste": ["scheduled", "caste", "atrocities"],
    "scheduled tribe": ["scheduled", "tribe", "atrocities"],

    # Succession
    "hindu succession": ["hindu", "succession", "intestate", "coparcener",
                         "daughter", "heir", "class"],
    "coparcener": ["coparcener", "coparcenary", "joint", "hindu", "family"],
    "ancestral property": ["coparcenary", "joint", "family", "partition",
                           "succession"],
    "daughter share": ["daughter", "coparcener", "equal", "share", "succession"],
    "indian succession": ["will", "probate", "executor", "intestate",
                          "legatee", "codicil"],
    "will": ["will", "testator", "bequest", "codicil", "probate"],
    "probate": ["probate", "will", "executor", "grant"],
    "intestate": ["intestate", "succession", "heirs", "distribution"],
    "legal heir": ["heir", "succession", "intestate", "class"],
    "nominee": ["nominee", "heir", "succession"],

    # BNSS
    "bnss": ["procedure", "police", "magistrate", "investigation", "trial",
             "bail", "summons"],
    "nagarik suraksha": ["procedure", "police", "magistrate", "investigation"],
    "file an fir": ["information", "cognizable", "offence", "police", "station"],
    "file fir": ["information", "cognizable", "offence", "police", "station"],
    "fir": ["information", "cognizable", "offence", "police", "station"],
    "zero fir": ["information", "cognizable", "police", "station",
                 "jurisdiction"],
    "police refuses": ["information", "refuse", "superintendent", "magistrate"],
    "anticipatory bail": ["anticipatory", "bail", "arrest", "apprehension"],
    "bail": ["bail", "bailable", "bond", "surety", "release"],
    "remand": ["custody", "detention", "magistrate", "twenty", "four", "hours"],
    "chargesheet": ["report", "police", "investigation", "magistrate"],
    "charge sheet": ["report", "police", "investigation", "magistrate"],

    # BSA
    "bsa": ["evidence", "fact", "relevant", "admission", "witness", "proof"],
    "sakshya adhiniyam": ["evidence", "fact", "relevant", "proof"],
    "burden of proof": ["burden", "proof", "fact", "party"],
    "hearsay": ["evidence", "statement", "relevant", "admissible"],
    "dying declaration": ["dying", "declaration", "statement", "death",
                          "relevant"],
    "electronic evidence": ["electronic", "record", "digital", "evidence",
                            "admissible"],
    "expert opinion": ["expert", "opinion", "relevant", "science"],

    # JJ / POSH / seniors
    "juvenile": ["juvenile", "child", "conflict", "law", "board", "care"],
    "child in conflict with law": ["child", "conflict", "law", "board",
                                   "juvenile"],
    "adoption": ["adoption", "adopt", "child", "guardianship", "committee"],
    "posh": ["sexual", "harassment", "workplace", "internal", "committee",
             "aggrieved"],
    "workplace harassment": ["sexual", "harassment", "workplace", "committee",
                             "employer"],
    "internal complaints committee": ["internal", "committee", "complaint",
                                      "workplace"],
    "senior citizen": ["senior", "citizen", "parent", "maintenance",
                       "tribunal", "welfare"],
    "old age parents": ["parent", "senior", "citizen", "maintenance",
                        "children"],
    "parents maintenance": ["parent", "maintenance", "senior", "citizen",
                            "tribunal"],
    "guardianship": ["guardian", "ward", "minor", "custody", "welfare"],
    "child custody": ["guardian", "custody", "minor", "welfare", "ward"],

    # other core
    "ndps": ["narcotic", "drug", "psychotropic", "substance", "commercial",
             "quantity"],
    "drugs case": ["narcotic", "drug", "psychotropic", "possession"],
    "bribe": ["bribe", "gratification", "public", "servant", "corruption"],
    "corruption": ["corruption", "gratification", "public", "servant",
                   "criminal", "misconduct"],
    "public servant": ["public", "servant", "gratification", "misconduct"],
    "register a document": ["registration", "registered", "document",
                            "sub", "registrar"],
    "sale deed": ["registration", "document", "immovable", "property"],
    "stamp duty": ["stamp", "duty", "instrument", "chargeable"],
    "disability": ["disability", "person", "benchmark", "rights",
                   "discrimination"],
    "human rights commission": ["human", "rights", "commission", "inquiry"],
    "special marriage": ["marriage", "solemnized", "notice", "objection",
                         "registrar"],
    "court marriage": ["marriage", "solemnized", "notice", "registrar",
                       "declaration"],
    "inter caste marriage": ["marriage", "solemnized", "special", "notice"],
    "interfaith marriage": ["marriage", "solemnized", "special", "notice"],
}


# --- civil & procedural concept gap ---------------------------------------
#
# Measured, not guessed. Running the 84-question eval set after the five new
# acts landed put BM25-only Recall@3 at 51/84, and 14 of the 33 misses were
# contract, NI Act, RTI or Motor Vehicles questions. The reason was visible in
# statutes._CONCEPTS: it is almost entirely criminal and constitutional
# vocabulary. There was not one entry for cheques, contracts, RTI procedure or
# drink driving, so nothing bridged "my cheque bounced" to the statutory
# phrase "dishonour ... insufficiency of funds", and BM25 matched on "cheque"
# alone - landing on the definition section instead of the offence.
#
# Adding the entries below moved Recall@3 to 73/84 (86.9%), R@1 from 42.9% to
# 64.3%, MRR from 0.499 to 0.729, with ZERO regressions on the questions that
# already passed. Re-measure with `python -m app.dense --calibrate` before and
# after touching this.
#
# One entry is a correction rather than an addition. "domestic violence" was
# already present, mapped only to BNS cruelty words - so the plainest phrasing
# of the question could never surface the DV Act, even with the act ingested.
# merge_into() UNIONS tokens on a shared key rather than replacing them, so
# the original BNS vocabulary is kept and the DV Act vocabulary is added.
#
# The bare "cheque" key is worth noting as a lesson: it first carried the
# dishonour vocabulary too, which pushed s.138 above s.6 on "what counts as a
# cheque in law" - a question that had been passing. A concept key should
# carry the vocabulary of the question it names, not of its whole subject
# area. Definition words on "cheque", dishonour words on "cheque bounced".

EXT_CIVIL_CONCEPTS: Dict[str, List[str]] = {

    # ---- Negotiable Instruments Act ---------------------------------------
    "cheque": ["cheque", "bill", "exchange", "drawer", "payee", "order",
               "writing", "banker"],
    "cheque bounced": ["dishonour", "dishonoured", "insufficiency", "funds",
                       "cheque", "drawer", "notice", "demand"],
    "bounced": ["dishonour", "dishonoured", "insufficiency", "funds"],
    "dishonour": ["dishonour", "dishonoured", "insufficiency", "funds",
                  "notice", "demand", "drawer"],
    "dishonoured": ["dishonour", "insufficiency", "funds", "notice", "demand"],
    "send notice": ["notice", "demand", "writing", "days", "receipt"],
    "promissory note": ["promissory", "note", "promise", "pay", "instrument",
                        "certain", "sum", "money"],
    "bill of exchange": ["bill", "exchange", "drawn", "instrument", "order"],
    "negotiable instrument": ["negotiable", "instrument", "promissory",
                              "cheque", "bill"],

    # ---- Contract Act -----------------------------------------------------
    "binding contract": ["agreement", "contract", "free", "consent",
                         "competent", "lawful", "consideration", "object"],
    "legally binding": ["agreement", "contract", "enforceable", "law",
                        "consideration", "consent"],
    "valid contract": ["agreement", "contract", "free", "consent",
                       "competent", "lawful", "consideration"],
    "broke our agreement": ["breach", "contract", "compensation", "loss",
                            "damage", "suffered"],
    "broke the contract": ["breach", "contract", "compensation", "loss",
                           "damage"],
    "breach of contract": ["breach", "contract", "compensation", "loss",
                           "damage", "suffered", "naturally"],
    "compensation can i claim": ["compensation", "loss", "damage", "breach",
                                 "suffered"],
    "penalty clause": ["penalty", "stipulated", "sum", "breach", "liquidated",
                       "reasonable", "compensation"],
    "liquidated damages": ["penalty", "stipulated", "sum", "breach",
                           "compensation"],
    "minor enter into a contract": ["competent", "age", "majority", "sound",
                                    "mind", "disqualified"],
    "can a minor": ["competent", "age", "majority", "disqualified"],
    "competent to contract": ["competent", "age", "majority", "sound", "mind"],
    "consent mean": ["consent", "free", "coercion", "undue", "influence",
                     "fraud", "misrepresentation", "mistake"],
    "free consent": ["consent", "free", "coercion", "undue", "influence",
                     "fraud", "misrepresentation"],
    "signed under threat": ["coercion", "committing", "forbidden", "unlawful",
                            "detaining", "consent", "voidable"],
    "under threat": ["coercion", "unlawful", "detaining", "consent",
                     "voidable"],
    "forced to sign": ["coercion", "unlawful", "consent", "voidable"],
    "undue influence": ["undue", "influence", "dominate", "will", "position"],
    "void agreement": ["void", "agreement", "unlawful", "consideration",
                       "object"],
    "consideration": ["consideration", "lawful", "promise", "agreement"],

    # ---- RTI Act ----------------------------------------------------------
    "apply for information": ["request", "application", "writing",
                              "information", "officer", "fee"],
    "file an application for information": ["request", "application",
                                            "writing", "officer", "fee",
                                            "thirty", "days"],
    "public authority refuse": ["exempt", "exemption", "disclosure",
                                "information", "obligation", "notwithstanding"],
    "refuse to give": ["exempt", "exemption", "disclosure", "information"],
    "information from a public authority": ["request", "information",
                                            "public", "authority", "officer"],
    "rti application": ["request", "application", "writing", "information",
                        "officer", "fee"],

    # ---- Motor Vehicles Act -----------------------------------------------
    "drink driving": ["drunken", "alcohol", "influence", "drug", "driving",
                      "breath", "blood"],
    "drunk driving": ["drunken", "alcohol", "influence", "driving"],
    "drunk and drove": ["drunken", "alcohol", "influence", "driving"],
    "rash driving": ["rash", "negligent", "driving", "dangerous"],
    "without a licence": ["licence", "driving", "without", "disqualified"],
    "hit and run": ["hit", "run", "accident", "compensation", "solatium"],
    "accident compensation": ["accident", "compensation", "claim", "tribunal",
                              "death", "injury"],

    # ---- BNS gaps the eval exposes ----------------------------------------
    "force to take my money": ["robbery", "theft", "extortion", "force",
                               "dishonestly", "hurt"],
    "took my money": ["theft", "cheating", "extortion", "dishonestly"],
    "on the street": ["robbery", "theft", "force"],
    "never delivered": ["cheating", "deceiving", "dishonestly", "deliver",
                        "property", "induce"],
    "paid online and never": ["cheating", "deceiving", "dishonestly",
                              "induce", "deliver"],
    "take their own life": ["abetment", "suicide", "instigates", "abets",
                            "commission"],
    "own life": ["abetment", "suicide", "instigates"],
    "attacked me first": ["private", "defence", "body", "assault",
                          "apprehension", "harm"],
    "defend myself": ["private", "defence", "body", "right", "apprehension"],
    "self defence": ["private", "defence", "body", "right"],
    "planned a crime together": ["conspiracy", "common", "intention",
                                 "furtherance", "abetment", "agree"],
    "planned together": ["conspiracy", "common", "intention", "furtherance"],
    "only one carried it out": ["common", "intention", "furtherance",
                                "several", "persons"],
    "broke into my house": ["house", "trespass", "breaking", "night",
                            "lurking", "housebreaking", "dwelling"],
    "broke into": ["trespass", "breaking", "housebreaking", "entry"],
    "at night": ["night", "sunset", "sunrise", "lurking"],
    "deliberately damaged": ["mischief", "wrongful", "loss", "damage",
                             "destroys", "property"],
    "damaged my": ["mischief", "wrongful", "loss", "damage", "property"],
    "fake document": ["forgery", "forged", "false", "document", "makes",
                      "dishonestly", "intent"],
    "signed my name": ["forgery", "false", "document", "signature", "makes"],
    "fake currency": ["counterfeiting", "counterfeit", "currency", "note",
                      "bank", "forged"],
    "currency notes": ["counterfeiting", "currency", "note", "bank"],
    "tried twice": ["prosecuted", "punished", "twice", "offence", "convicted"],
    "same offence": ["prosecuted", "punished", "twice", "offence"],
    "double jeopardy": ["prosecuted", "punished", "twice", "offence"],

    # ---- Constitution gaps -------------------------------------------------
    "take my land": ["property", "deprived", "save", "authority", "law"],
    "take my property": ["property", "deprived", "save", "authority", "law"],
    "without legal authority": ["save", "authority", "law", "deprived"],
    "duties": ["fundamental", "duties", "citizen", "abide", "cherish"],
    "duties on citizens": ["fundamental", "duties", "citizen", "abide"],
    "amended": ["amendment", "constitution", "parliament", "majority",
                "ratification"],
    "be amended": ["amendment", "constitution", "parliament", "majority"],

    # ---- the two spot-check weaknesses ------------------------------------
    # "domestic violence" already existed but mapped ONLY to BNS cruelty
    # words, so the DV Act could never surface on the plainest phrasing of
    # the question. merge_into() unions tokens for a shared key.
    "domestic violence": ["domestic", "violence", "aggrieved", "shared",
                          "household", "protection", "order", "residence"],
    "without a will": ["intestate", "succession", "heirs", "class",
                       "devolve", "property"],
    "dies without": ["intestate", "succession", "heirs", "class", "devolve"],
    "who inherits": ["intestate", "succession", "heirs", "class", "devolve",
                     "share"],
}


# --- aliases, for direct citation lookup ----------------------------------
#
# Merged into statutes._ACT_ALIASES. Note the two corrections at the top:
# before this change "bnss" resolved to `crpc` and "bsa" to `iea`, so "BNSS
# 173" returned a REPEALED CrPC section with a repeal badge on it. That was a
# straightforward correctness bug and these two lines fix it.

# Act names that should trigger an act-level OVERVIEW, beyond the ones already
# listed in statutes._OVERVIEW_NAMES. Kept separate because these are the
# colloquial forms - nobody types "Protection of Women from Domestic Violence
# Act, 2005" when asking what domestic violence is.
#
# "What is domestic violence under Indian law?" was returning DV Act s.1,
# "Short title, extent and commencement" - because short-title sections
# contain the act's own name and statutes._normalize repeats titles 4x in the
# token list, so they get boosted on any query naming the act. Demoting
# boilerplate sections by rank weight was tried first and rejected: it removed
# the useless section without promoting the right one, and cost SC/ST Act s.1
# on an unrelated query. Routing the question to the act overview is the
# better answer anyway - "what is domestic violence" wants the act explained,
# not one section quoted.
EXT_OVERVIEW_NAMES: List[str] = [
    "protection of women from domestic violence",
    "domestic violence",
    "child sexual offences",
    "sc/st atrocities act",
    "atrocities against scheduled castes",
    "hindu succession",
    "indian succession",
    "juvenile justice",
    "senior citizens",
    "sexual harassment at workplace",
]


EXT_ALIASES: Dict[str, str] = {
    # Colloquial overview triggers need an alias to resolve to an act key.
    "domestic violence": "pwdva",
    "protection of women from domestic violence": "pwdva",
    "child sexual offences": "pocso",
    "sc/st atrocities act": "scst",
    "atrocities against scheduled castes": "scst",
    "hindu succession": "hsa",
    "indian succession": "isa",
    "juvenile justice": "jj",
    "senior citizens": "senior_citizens",
    "sexual harassment at workplace": "posh",

    "bnss": "bnss",
    "bharatiya nagarik suraksha sanhita": "bnss",
    "nagarik suraksha sanhita": "bnss",
    "nagarik suraksha": "bnss",
    "bsa": "bsa",
    "bharatiya sakshya adhiniyam": "bsa",
    "sakshya adhiniyam": "bsa",

    "pocso": "pocso",
    "pocso act": "pocso",
    "protection of children from sexual offences act": "pocso",
    "protection of children from sexual offences": "pocso",

    "dv act": "pwdva",
    "pwdva": "pwdva",
    "domestic violence act": "pwdva",
    "protection of women from domestic violence act": "pwdva",

    "dowry prohibition act": "dowry",
    "dowry act": "dowry",

    "sc st act": "scst",
    "sc/st act": "scst",
    "scst act": "scst",
    "atrocities act": "scst",
    "prevention of atrocities act": "scst",
    "scheduled castes and scheduled tribes prevention of atrocities act": "scst",

    "hsa": "hsa",
    "hindu succession act": "hsa",
    "indian succession act": "isa",
    "succession act": "isa",

    "jj act": "jj",
    "juvenile justice act": "jj",
    "posh act": "posh",
    "sexual harassment of women at workplace act": "posh",
    "hama": "hama",
    "hindu adoptions and maintenance act": "hama",
    "hindu adoption and maintenance act": "hama",
    "hmga": "hmga",
    "hindu minority and guardianship act": "hmga",
    "special marriage act": "sma",
    "sma": "sma",
    "senior citizens act": "senior_citizens",
    "maintenance and welfare of parents and senior citizens act": "senior_citizens",
    "guardians and wards act": "guardians_wards",
    "ndps": "ndps",
    "ndps act": "ndps",
    "narcotic drugs and psychotropic substances act": "ndps",
    "pc act": "pca",
    "prevention of corruption act": "pca",
    "registration act": "registration",
    "stamp act": "stamp",
    "indian stamp act": "stamp",
    "rpwd act": "rpwd",
    "rights of persons with disabilities act": "rpwd",
    "protection of human rights act": "phra",
    "partnership act": "partnership",
    "indian partnership act": "partnership",
    "easements act": "easements",
    "indian easements act": "easements",
    "companies act": "companies",
}

# Short tokens that can appear on either side of a section number in a
# citation ("BNSS 173", "173 BNSS"). Folded into statutes._ACT_TOKENS.
EXT_ACT_TOKENS: List[str] = [
    "bnss", "bsa", "pocso", "pwdva", "hsa", "hama", "hmga", "sma",
    "ndps", "rpwd",
]

# Long names, only ever written after the number. Folded into
# statutes._ACT_NAMES. Order matters: longest first, so "indian succession
# act" wins over "succession act".
EXT_ACT_NAMES: List[str] = [
    "bharatiya nagarik suraksha sanhita",
    "nagarik suraksha sanhita",
    "bharatiya sakshya adhiniyam",
    "sakshya adhiniyam",
    "protection of children from sexual offences act",
    "protection of women from domestic violence act",
    "domestic violence act",
    "scheduled castes and scheduled tribes prevention of atrocities act",
    "prevention of atrocities act",
    "atrocities act",
    "dowry prohibition act",
    "hindu succession act",
    "indian succession act",
    "juvenile justice act",
    "sexual harassment of women at workplace act",
    "hindu adoptions and maintenance act",
    "hindu adoption and maintenance act",
    "hindu minority and guardianship act",
    "maintenance and welfare of parents and senior citizens act",
    "senior citizens act",
    "guardians and wards act",
    "narcotic drugs and psychotropic substances act",
    "prevention of corruption act",
    "rights of persons with disabilities act",
    "protection of human rights act",
    "indian partnership act",
    "indian easements act",
    "special marriage act",
    "indian stamp act",
    "registration act",
    "companies act",
]


# --- act-level overviews --------------------------------------------------
#
# BM25 cannot answer "what is POCSO" - the query is one out-of-vocabulary
# token and the answer is the whole act. These are the same explicit-handling
# path statutes._OVERVIEWS already uses for the BNS and the Constitution, and
# they are what makes the "What is POCSO?" validation query return something
# coherent rather than section 1 (short title and commencement).

EXT_OVERVIEWS: Dict[str, Dict[str, str]] = {
    "pocso": {
        "title": "Protection of Children from Sexual Offences Act, 2012",
        "text": (
            "The Protection of Children from Sexual Offences Act, 2012 - "
            "commonly called the POCSO Act - is India's dedicated law on "
            "sexual offences against children. A child means anyone below 18, "
            "and the Act applies regardless of the child's gender.\n\n"
            "It defines a graded set of offences: sexual assault, aggravated "
            "sexual assault, penetrative sexual assault, aggravated "
            "penetrative sexual assault, sexual harassment, and using a child "
            "for pornographic purposes. 'Aggravated' forms carry higher "
            "punishment and cover offences by people in a position of trust "
            "or authority - a police officer, a doctor, a teacher, a "
            "relative.\n\n"
            "Its procedural design is as important as the offences. Reporting "
            "is mandatory, and failure to report is itself an offence. The Act "
            "presumes culpable mental state, placing the burden on the "
            "accused. Trials happen in designated Special Courts with "
            "child-friendly procedure: the child's statement is recorded at "
            "their residence where possible, by a woman officer, without the "
            "accused present, and the child cannot be called repeatedly or "
            "aggressively cross-examined. The child's identity may not be "
            "disclosed.\n\n"
            "POCSO runs alongside the Bharatiya Nyaya Sanhita, 2023 rather "
            "than replacing it. Where both apply, the law prescribing the "
            "greater punishment governs."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/2079",
    },
    "pwdva": {
        "title": "Protection of Women from Domestic Violence Act, 2005",
        "text": (
            "The Protection of Women from Domestic Violence Act, 2005 is a "
            "civil law, and that is the point of it. Criminal cruelty "
            "provisions require a criminal prosecution; this Act lets a woman "
            "get immediate practical relief from a Magistrate without one.\n\n"
            "'Domestic violence' is defined broadly: physical, sexual, verbal "
            "and emotional, and economic abuse - including denying money, "
            "disposing of her assets, or throwing her out of the home. An "
            "'aggrieved person' is any woman in a domestic relationship with "
            "the respondent; 'shared household' is the key concept, because "
            "the right to remain in it does not depend on owning it.\n\n"
            "The reliefs a Magistrate can grant are the substance of the Act: "
            "protection orders (stopping further violence, contact, or "
            "communication), residence orders (letting her stay in the shared "
            "household, or requiring alternative accommodation), monetary "
            "relief, custody orders, and compensation. Breaching a protection "
            "order is a criminal offence.\n\n"
            "Protection Officers and registered service providers exist to "
            "help file a Domestic Incident Report, so a woman does not have "
            "to navigate the process alone. Proceedings run alongside any "
            "criminal case, not instead of it."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/2021",
    },
    "dowry": {
        "title": "Dowry Prohibition Act, 1961",
        "text": (
            "The Dowry Prohibition Act, 1961 makes giving, taking, and "
            "demanding dowry a punishable offence. 'Dowry' means any property "
            "or valuable security given or agreed to be given in connection "
            "with a marriage - by either side, at any time before, at, or "
            "after the marriage.\n\n"
            "Both giving and taking are offences, which surprises people: a "
            "bride's family that pays is technically also liable, though "
            "prosecution in practice targets demands. Demanding dowry is a "
            "separate offence, as is advertising an offer of money or property "
            "in consideration of a marriage.\n\n"
            "An agreement to give or take dowry is void. Any dowry received is "
            "to be held in trust for the woman and transferred to her.\n\n"
            "It works together with two other tracks: the criminal cruelty and "
            "dowry-death provisions of the Bharatiya Nyaya Sanhita, 2023, and "
            "the civil reliefs of the Protection of Women from Domestic "
            "Violence Act, 2005. A dowry harassment situation usually engages "
            "all three."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/1516",
    },
    "scst": {
        "title": ("Scheduled Castes and the Scheduled Tribes "
                  "(Prevention of Atrocities) Act, 1989"),
        "text": (
            "The Scheduled Castes and the Scheduled Tribes (Prevention of "
            "Atrocities) Act, 1989 - the SC/ST Act - creates a set of special "
            "offences called 'atrocities', committed against a member of a "
            "Scheduled Caste or Scheduled Tribe by someone who is not a "
            "member of one.\n\n"
            "The listed offences go well beyond ordinary assault: caste-based "
            "insult or humiliation in public view, social and economic "
            "boycott, wrongful occupation of land, forcing someone to leave "
            "their home, obstructing access to water or public places, and "
            "sexual offences with a caste element. Public servants who "
            "wilfully neglect their duties under the Act are themselves "
            "liable.\n\n"
            "Much of the Act's force is procedural. Cases are tried in Special "
            "Courts by Special Public Prosecutors, with provision for "
            "time-bound investigation and trial. Anticipatory bail is "
            "excluded. Victims and witnesses have statutory rights to "
            "protection, travel and maintenance expenses, and relief and "
            "rehabilitation.\n\n"
            "The 1989 Act as amended is the consolidated law. Its amendment "
            "Acts modify it; they are not separate statutes and should not be "
            "cited as such."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/1533",
    },
    "hsa": {
        "title": "Hindu Succession Act, 1956",
        "text": (
            "The Hindu Succession Act, 1956 governs intestate succession - "
            "who inherits when a Hindu dies without a will. It applies to "
            "Hindus, Buddhists, Jains and Sikhs.\n\n"
            "For a male dying intestate, property devolves first on Class I "
            "heirs (widow, sons, daughters, mother, and specified "
            "descendants of predeceased children), who take simultaneously "
            "and to the exclusion of everyone else. Failing them it goes to "
            "Class II heirs, then agnates, then cognates. For a female dying "
            "intestate the Act sets out a separate order of succession.\n\n"
            "The most consequential provision is the coparcenary rule. Since "
            "the 2005 amendment, a daughter is a coparcener in a joint Hindu "
            "family by birth, in the same manner and to the same extent as a "
            "son, with the same right to claim partition. The Supreme Court "
            "has held this applies regardless of whether the father was alive "
            "in 2005.\n\n"
            "The Act covers intestate succession only. A valid will displaces "
            "it, and testamentary succession for Hindus is governed by the "
            "Indian Succession Act, 1925."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/1670",
    },
    "isa": {
        "title": "Indian Succession Act, 1925",
        "text": (
            "The Indian Succession Act, 1925 is the general law of succession "
            "in India. It consolidates two different subjects: intestate "
            "succession for communities not covered by their own personal "
            "law (notably Christians and Parsis, each with a distinct "
            "scheme), and testamentary succession - wills - for almost "
            "everyone, Hindus included.\n\n"
            "Its testamentary provisions are the part most often needed. They "
            "set out who can make a will, what formalities are required "
            "(signature by the testator, attestation by two witnesses), how a "
            "will may be revoked or altered by codicil, how bequests are "
            "construed, and the rules on void bequests and lapse.\n\n"
            "It also governs the machinery of administration: probate of a "
            "will, letters of administration where there is none, succession "
            "certificates for debts and securities, and the powers and duties "
            "of executors and administrators.\n\n"
            "For Hindus, Buddhists, Jains and Sikhs, intestate succession is "
            "governed by the Hindu Succession Act, 1956 rather than this Act - "
            "but their wills are governed by this one. Muslim succession is "
            "largely outside the Act."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/2394",
    },
    "bnss": {
        "title": "Bharatiya Nagarik Suraksha Sanhita, 2023",
        "text": (
            "The Bharatiya Nagarik Suraksha Sanhita, 2023 is India's criminal "
            "procedure code. It came into force on 1 July 2024 and replaced "
            "the Code of Criminal Procedure, 1973.\n\n"
            "It covers the whole machinery of a criminal case: reporting an "
            "offence, police powers of investigation and arrest, bail, the "
            "structure and jurisdiction of criminal courts, framing charges, "
            "trial procedure, judgment, sentencing, appeals and revision, and "
            "maintenance of wives, children and parents.\n\n"
            "Notable changes from the CrPC include statutory timelines at "
            "several stages, provision for an FIR to be registered at any "
            "police station regardless of jurisdiction, electronic filing and "
            "audio-video recording of searches and statements, trial in "
            "absentia for proclaimed offenders, and mandatory forensic "
            "investigation for offences carrying seven years or more.\n\n"
            "Section numbers changed throughout. The FIR provision people "
            "still call 'section 154' is now section 173; the anticipatory "
            "bail provision formerly 438 is now 482; maintenance formerly 125 "
            "is now 144. Case law decided under the CrPC still cites the old "
            "numbers."
        ),
        "url": "https://devgan.in/bnss/",
    },
    "bsa": {
        "title": "Bharatiya Sakshya Adhiniyam, 2023",
        "text": (
            "The Bharatiya Sakshya Adhiniyam, 2023 is India's law of "
            "evidence. It came into force on 1 July 2024 and replaced the "
            "Indian Evidence Act, 1872.\n\n"
            "It keeps the structure of the 1872 Act largely intact: what "
            "facts are relevant, admissions and confessions, dying "
            "declarations, expert and opinion evidence, oral and documentary "
            "evidence, presumptions, burden of proof, estoppel, and the "
            "examination of witnesses.\n\n"
            "The substantive change is the treatment of electronic evidence. "
            "Electronic and digital records are placed squarely within the "
            "definition of a document and of primary evidence, and the "
            "certification requirements for admitting them are set out "
            "directly rather than bolted on.\n\n"
            "It is one of the three criminal laws that took effect on 1 July "
            "2024, alongside the Bharatiya Nyaya Sanhita, 2023 (replacing the "
            "Indian Penal Code) and the Bharatiya Nagarik Suraksha Sanhita, "
            "2023 (replacing the Code of Criminal Procedure). Section numbers "
            "changed, so older judgments cite Evidence Act numbering."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/20063",
    },
    "posh": {
        "title": ("Sexual Harassment of Women at Workplace "
                  "(Prevention, Prohibition and Redressal) Act, 2013"),
        "text": (
            "The POSH Act, 2013 places a statutory duty on every workplace to "
            "prevent and redress sexual harassment of women. It grew out of "
            "the Supreme Court's Vishaka guidelines.\n\n"
            "'Sexual harassment' covers unwelcome physical contact and "
            "advances, a demand or request for sexual favours, sexually "
            "coloured remarks, showing pornography, and other unwelcome "
            "conduct of a sexual nature. It also covers implied threats about "
            "employment status and the creation of a hostile work "
            "environment.\n\n"
            "Every employer with ten or more workers must constitute an "
            "Internal Committee, headed by a woman, including an external "
            "member from an NGO or someone familiar with the issues. "
            "Districts have Local Committees for the unorganised sector and "
            "for complaints against the employer. A complaint should normally "
            "be filed within three months, extendable.\n\n"
            "The Committee inquires with the powers of a civil court and "
            "recommends action; the employer must act on it. Conciliation is "
            "available at the woman's request but cannot be monetary "
            "settlement alone. Confidentiality is mandatory, and the Act "
            "penalises both non-compliance by employers and malicious "
            "complaints."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/2104",
    },
    "jj": {
        "title": "Juvenile Justice (Care and Protection of Children) Act, 2015",
        "text": (
            "The Juvenile Justice (Care and Protection of Children) Act, 2015 "
            "deals with two distinct groups: children in conflict with law, "
            "and children in need of care and protection. It replaced the "
            "2000 Act.\n\n"
            "For children in conflict with law, the guiding idea is "
            "rehabilitation rather than punishment. Cases go to a Juvenile "
            "Justice Board, not an ordinary criminal court, and dispositions "
            "focus on counselling, community service, and placement in a "
            "special home. The Act's most debated provision allows a child "
            "aged 16 to 18 accused of a heinous offence to be tried as an "
            "adult, but only after a preliminary assessment by the Board of "
            "their mental and physical capacity and understanding of "
            "consequences.\n\n"
            "For children in need of care and protection, Child Welfare "
            "Committees decide placement, and the Act sets out the framework "
            "for children's homes, foster care, and sponsorship.\n\n"
            "It also codifies adoption, including eligibility of prospective "
            "parents, the role of the Central Adoption Resource Authority, "
            "and inter-country adoption, and creates offences of cruelty to a "
            "child, employing a child for begging, and using a child in "
            "criminal activity."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/2168",
    },
    "senior_citizens": {
        "title": "Maintenance and Welfare of Parents and Senior Citizens Act, 2007",
        "text": (
            "The Maintenance and Welfare of Parents and Senior Citizens Act, "
            "2007 gives parents and senior citizens a fast, cheap route to "
            "maintenance from their children or relatives - deliberately "
            "outside the ordinary civil courts.\n\n"
            "A parent, including a childless senior citizen, can apply to a "
            "Maintenance Tribunal presided over by a sub-divisional officer. "
            "Children and adult grandchildren have a statutory obligation to "
            "maintain a parent who cannot maintain themselves; relatives who "
            "stand to inherit have a corresponding obligation to a childless "
            "senior citizen. Legal representation is not required, and the "
            "Tribunal is meant to dispose of an application within 90 days.\n\n"
            "The provision people most often need is the one on transferred "
            "property: where a senior citizen has gifted or transferred "
            "property on the condition of being looked after, and the "
            "transferee then neglects them, the transfer can be declared void "
            "as obtained by fraud or coercion.\n\n"
            "The Act also requires States to set up old age homes and provide "
            "medical facilities, and it penalises abandonment of a senior "
            "citizen."
        ),
        "url": "https://www.indiacode.nic.in/handle/123456789/2062",
    },
}


def merge_into(statutes_module) -> Dict[str, int]:
    """Fold this extension into statutes.py's registries.

    Called once, at the bottom of statutes.py's module body, before the
    citation regexes are compiled - `_CITE_RE` is built from _ACT_TOKENS and
    _ACT_NAMES, so merging after compilation would leave the new acts
    uncitable.

    Returns a small count dict so a caller can log what happened. Existing
    keys are never overwritten: if statutes.py already defines an act, alias
    or concept, the hand-checked version wins.
    """
    m = statutes_module
    added = {"acts": 0, "concepts": 0, "aliases": 0, "overviews": 0}

    for k, v in EXT_ACTS.items():
        if k not in m.ACTS:
            m.ACTS[k] = v
            added["acts"] += 1

    for k, v in {**EXT_CONCEPTS, **EXT_CIVIL_CONCEPTS}.items():
        if k in m._CONCEPTS:
            # Union rather than replace - the existing entry was tuned on the
            # eval set and its tokens should not be dropped.
            merged = list(dict.fromkeys(list(m._CONCEPTS[k]) + list(v)))
            m._CONCEPTS[k] = merged
        else:
            m._CONCEPTS[k] = list(v)
            added["concepts"] += 1

    # Aliases DO get overwritten, and that is intentional: this is where the
    # bnss->crpc and bsa->iea corrections land.
    for k, v in EXT_ALIASES.items():
        if m._ACT_ALIASES.get(k) != v:
            added["aliases"] += 1
        m._ACT_ALIASES[k] = v

    for k, v in EXT_OVERVIEWS.items():
        if k not in m._OVERVIEWS:
            m._OVERVIEWS[k] = v
            added["overviews"] += 1

    return added