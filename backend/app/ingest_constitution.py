"""Ingest the Constitution of India into backend/data/coi.json.

Source: https://huggingface.co/datasets/Sharathhebbar24/Indian-Constitution
454 rows, columns `article_id` ("Article 21 of Indian Constitution") and
`article_desc` (marginal note + full text, concatenated).

Run from the backend directory:

    python -m app.ingest_constitution

Two jobs beyond downloading:
  1. Pull the article number out of `article_id`, keeping letter suffixes
     (21A, 31B, 51A) since those are real articles, not typos.
  2. Split the marginal note off the front of `article_desc` so we get a
     usable title. The dataset concatenates them: "Equality before law The
     State shall not deny..." - title is everything before the body starts.

The Preamble is added by hand; it isn't an article, so it isn't in the
dataset, but people ask for it constantly.
"""

import json
import re
import sys
from pathlib import Path

import httpx

ROWS_URL = "https://datasets-server.huggingface.co/rows"
DATASET = "Sharathhebbar24/Indian-Constitution"
PAGE = 100

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT = DATA_DIR / "coi.json"

# Suffix must be immediately adjacent and not part of a following word:
# "Article 21A of ..." -> 21A, but "Article 12 of ..." -> 12 (not "12O")
_ARTICLE_RE = re.compile(r"Article\s+(\d+)([A-Za-z]?)(?![A-Za-z])", re.IGNORECASE)

# Substantive text almost always opens with one of these. Everything before
# the first one is the marginal note.
_ANCHORS = re.compile(
    r"(?=\((?:1|a|i)\)"
    r"|\bThe State\b|\bNo person\b|\bNo citizen\b|\bAll citizens\b|\bAll minorities\b"
    r"|\bEvery \b|\bThere shall\b|\bParliament\b|\bSubject to\b|\bNothing in\b"
    r"|\bNotwithstanding\b|\bAny \b|\bIn this [Pp]art\b|\bWhoever\b"
    r"|\bAt the commencement\b|\bUpon the completion\b|\bA person\b|\bWhile the\b"
    r"|\bIt shall be\b|\bThe provisions\b|\bWhen a\b|\bThis Constitution\b"
    r"|\bTraffic in\b|\bNo child\b|\bNo religion\b|\bWithout prejudice\b"
    r"|\bThe right\b|\bThe President\b|\bThe Vice\b|\bThe Council\b|\bThe House\b"
    r"|\bThe Speaker\b|\bThe Chairman\b|\bNo title\b|\bNo Bill\b)"
)

# "Abolition of Untouchability Untouchability is abolished" - the note's last
# word is repeated as the body's first word. Split between the two.
_REPEAT = re.compile(r"\b(\w{4,})\s+\1\b")

PARTS = [
    (1, 4, "Part I — The Union and its Territory"),
    (5, 11, "Part II — Citizenship"),
    (12, 35, "Part III — Fundamental Rights"),
    (36, 51, "Part IV — Directive Principles of State Policy"),
    (52, 151, "Part V — The Union"),
    (152, 237, "Part VI — The States"),
    (238, 242, "Part VII / VIII — Union Territories"),
    (243, 243, "Part IX — The Panchayats"),
    (244, 244, "Part X — Scheduled and Tribal Areas"),
    (245, 263, "Part XI — Relations between the Union and the States"),
    (264, 300, "Part XII — Finance, Property, Contracts and Suits"),
    (301, 307, "Part XIII — Trade, Commerce and Intercourse"),
    (308, 323, "Part XIV — Services under the Union and the States"),
    (324, 329, "Part XV — Elections"),
    (330, 342, "Part XVI — Special Provisions for Certain Classes"),
    (343, 351, "Part XVII — Official Language"),
    (352, 360, "Part XVIII — Emergency Provisions"),
    (361, 367, "Part XIX — Miscellaneous"),
    (368, 368, "Part XX — Amendment of the Constitution"),
    (369, 392, "Part XXI — Temporary, Transitional and Special Provisions"),
    (393, 395, "Part XXII — Short Title, Commencement and Repeals"),
]


def part_for(num: int) -> str:
    for lo, hi, name in PARTS:
        if lo <= num <= hi:
            return name
    return ""


def split_note(desc: str):
    """Separate the marginal note (title) from the substantive text."""
    desc = desc.strip()

    m = _REPEAT.search(desc)
    if m and m.start() <= 160:
        cut = m.start() + len(m.group(1))
        return desc[:cut].strip(" :-—"), desc[cut:].strip()

    for m in _ANCHORS.finditer(desc):
        if 0 < m.start() <= 160:
            return desc[: m.start()].strip(" :-—"), desc[m.start():].strip()

    words = desc.split()
    return " ".join(words[:12]).strip(" :-—"), desc


PREAMBLE = {
    "chapter_title": "Preamble",
    "Section": "Preamble",
    "section_title": "Preamble to the Constitution of India",
    "section_desc": (
        "WE, THE PEOPLE OF INDIA, having solemnly resolved to constitute India "
        "into a SOVEREIGN SOCIALIST SECULAR DEMOCRATIC REPUBLIC and to secure "
        "to all its citizens:\n\n"
        "JUSTICE, social, economic and political;\n"
        "LIBERTY of thought, expression, belief, faith and worship;\n"
        "EQUALITY of status and of opportunity;\n"
        "and to promote among them all\n"
        "FRATERNITY assuring the dignity of the individual and the unity and "
        "integrity of the Nation;\n\n"
        "IN OUR CONSTITUENT ASSEMBLY this twenty-sixth day of November, 1949, "
        "do HEREBY ADOPT, ENACT AND GIVE TO OURSELVES THIS CONSTITUTION.\n\n"
        "(The words 'SOCIALIST SECULAR' and 'and integrity' were inserted by "
        "the Constitution (Forty-second Amendment) Act, 1976, with effect from "
        "3 January 1977.)"
    ),
}

# Articles inserted after the source dataset's text was compiled. 21A came
# in with the 86th Amendment (2002), 338A with the 89th (2003).
EXTRA_ARTICLES = [
    {
        "chapter_title": "Part III — Fundamental Rights",
        "Section": "21A",
        "section_title": "Right to education",
        "section_desc": (
            "The State shall provide free and compulsory education to all "
            "children of the age of six to fourteen years in such manner as "
            "the State may, by law, determine."
        ),
    },
    {
        "chapter_title": "Part XVI — Special Provisions for Certain Classes",
        "Section": "338A",
        "section_title": "National Commission for Scheduled Tribes",
        "section_desc": (
            "(1) There shall be a Commission for the Scheduled Tribes to be "
            "known as the National Commission for the Scheduled Tribes.\n"
            "(2) The Commission shall consist of a Chairperson, Vice-Chairperson "
            "and three other Members, appointed by the President by warrant "
            "under his hand and seal.\n"
            "(5) It shall be the duty of the Commission to investigate and "
            "monitor all matters relating to the safeguards provided for the "
            "Scheduled Tribes under this Constitution or under any law or order "
            "of the Government; to inquire into specific complaints with respect "
            "to the deprivation of rights and safeguards of the Scheduled Tribes; "
            "to participate and advise on the planning process of socio-economic "
            "development of the Scheduled Tribes; and to present reports to the "
            "President upon the working of those safeguards.\n"
            "(8) The Commission shall, while investigating any matter or "
            "inquiring into any complaint, have all the powers of a civil court "
            "trying a suit."
        ),
    },
]

def pick(row: dict, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    lowered = {k.lower(): v for k, v in row.items()}
    for n in names:
        v = lowered.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    offset = 0
    total = None

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        while True:
            resp = client.get(
                ROWS_URL,
                params={
                    "dataset": DATASET,
                    "config": "default",
                    "split": "train",
                    "offset": offset,
                    "length": PAGE,
                },
            )
            if resp.status_code != 200:
                print(f"datasets-server returned {resp.status_code}: {resp.text[:300]}")
                return 1

            payload = resp.json()
            total = payload.get("num_rows_total", total)
            batch = payload.get("rows", [])
            if not batch:
                break

            rows.extend(r["row"] for r in batch)
            offset += len(batch)
            print(f"  fetched {offset}/{total or '?'}")

            if total and offset >= total:
                break

    if not rows:
        print("No rows returned. Check the dataset name and that it's still public.")
        return 1

    out = [PREAMBLE] + [dict(a) for a in EXTRA_ARTICLES]
    skipped = 0

    for row in rows:
        aid = pick(row, "article_id", "Article", "article")
        desc = pick(row, "article_desc", "description", "text")
        if not aid or not desc:
            skipped += 1
            continue

        m = _ARTICLE_RE.search(str(aid))
        if not m:
            skipped += 1
            continue

        num, suffix = m.group(1), (m.group(2) or "").upper()
        label = f"{num}{suffix}"
        title, body = split_note(str(desc))

        out.append(
            {
                "chapter_title": part_for(int(num)),
                "Section": label,
                "section_title": title,
                "section_desc": body,
            }
        )

    # Sort numerically, keeping the Preamble first and 21 before 21A.
    def key(r):
        s = str(r["Section"])
        if s == "Preamble":
            return (-1, "")
        m = re.match(r"(\d+)([A-Z]*)", s)
        return (int(m.group(1)), m.group(2))

    out.sort(key=key)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nWrote {len(out)} entries to {OUT} (including the Preamble)")
    if skipped:
        print(f"Skipped {skipped} malformed rows.")

    # Sanity check the articles people actually ask about.
    have = {str(r["Section"]) for r in out}
    want = ["Preamble", "14", "15", "19", "21", "21A", "22", "32", "51A", "368"]
    missing = [w for w in want if w not in have]
    if missing:
        print(f"WARNING - expected entries not found: {missing}")
    else:
        print("Key articles present: " + ", ".join(want))

    print("\nRestart the API so the statute index reloads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())