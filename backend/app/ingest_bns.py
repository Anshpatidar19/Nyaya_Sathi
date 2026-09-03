"""Ingest the BNS bare act into backend/data/bns.json.

Run from the backend directory:

    python -m app.ingest_bns

Source: https://huggingface.co/datasets/navaneeth005/BNS_definitions
358 rows, one per section, curated from the official India Code text.

We resolve the filename via the Hub API rather than hardcoding it, so the
script keeps working if the uploader renames or reformats the file. No
`datasets` dependency - httpx is already in requirements.txt.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx

REPO = "navaneeth005/BNS_definitions"
TREE_API = f"https://huggingface.co/api/datasets/{REPO}/tree/main"
RESOLVE = f"https://huggingface.co/datasets/{REPO}/resolve/main"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT = DATA_DIR / "bns.json"

# The viewer shows "Section" / "Title" / "Legal Definition"; the dataset card
# shows lowercase "section" / "title" / "definition". Accept either.
_NUM_KEYS = ("Section", "section", "section_number", "Section Number")
_TITLE_KEYS = ("Title", "title", "section_title")
_BODY_KEYS = ("Legal Definition", "legal_definition", "definition", "Definition", "section_desc", "text")


def _pick(row: Dict[str, Any], keys) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _load_any(raw: bytes) -> List[Dict[str, Any]]:
    """Parse either a JSON array or JSONL."""
    text = raw.decode("utf-8").strip()
    if not text:
        return []
    if text.lstrip().startswith("["):
        data = json.loads(text)
        return data if isinstance(data, list) else []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=90.0, follow_redirects=True) as client:
        print(f"Listing files in {REPO} ...")
        try:
            listing = client.get(TREE_API).raise_for_status().json()
        except Exception as exc:
            print(f"Could not reach the Hugging Face Hub API: {exc}")
            print("Download the dataset file manually and drop it at:")
            print(f"  {OUT}")
            return 1

        candidates = [
            f["path"] for f in listing
            if isinstance(f, dict)
            and f.get("type") == "file"
            and f.get("path", "").lower().endswith((".json", ".jsonl"))
            and not f.get("path", "").lower().endswith((".gitattributes",))
        ]
        if not candidates:
            print("No .json/.jsonl file found in the repo. Files present:")
            for f in listing:
                print("  ", f.get("path"))
            return 1

        # Prefer the largest data file; README-adjacent configs are tiny.
        candidates.sort(key=lambda p: 0 if "readme" in p.lower() else 1, reverse=True)
        rows: List[Dict[str, Any]] = []
        used = None
        for path in candidates:
            print(f"Fetching {path} ...")
            try:
                raw = client.get(f"{RESOLVE}/{path}").raise_for_status().content
            except Exception as exc:
                print(f"  skipped ({exc})")
                continue
            parsed = _load_any(raw)
            if len(parsed) > len(rows):
                rows, used = parsed, path

    if not rows:
        print("Downloaded the repo but found no parseable rows.")
        return 1

    out: List[Dict[str, Any]] = []
    skipped = 0
    for row in rows:
        num = _pick(row, _NUM_KEYS)
        body = _pick(row, _BODY_KEYS)
        if num is None or not body:
            skipped += 1
            continue
        out.append(
            {
                "Section": int(str(num).strip()) if str(num).strip().isdigit() else str(num).strip(),
                "section_title": (_pick(row, _TITLE_KEYS) or "").strip(),
                "section_desc": str(body).strip(),
            }
        )

    out.sort(key=lambda r: (isinstance(r["Section"], str), r["Section"]))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    nums = {r["Section"] for r in out if isinstance(r["Section"], int)}
    missing = [n for n in range(1, 359) if n not in nums]

    print(f"\nSource file : {used}")
    print(f"Written     : {OUT}  ({len(out)} sections)")
    if skipped:
        print(f"Skipped     : {skipped} rows with no section number or body")
    if missing:
        print(f"WARNING: {len(missing)} of sections 1-358 missing: {missing[:20]}"
              f"{' ...' if len(missing) > 20 else ''}")
    else:
        print("All 358 sections present.")

    print("\nRestart the API so the statute index reloads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())