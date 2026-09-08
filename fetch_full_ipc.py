"""
Replace backend/data/ipc.json (currently ~12 sections) with the full IPC 1860.

Source: github.com/civictech-India/Indian-Law-Penal-Code-Json  (575 sections,
including lettered ones like 124A, 153AA, 376DA).

The script reads your EXISTING ipc.json first and copies its schema, so the
output drops into your current parser and BM25 pipeline without changes.

Usage:
    python fetch_full_ipc.py                      # dry run, prints plan only
    python fetch_full_ipc.py --write              # writes (backs up old file)
    python fetch_full_ipc.py --write --act crpc   # same for crpc/cpc/iea/nia/hma/ida/MVA
"""

import argparse
import json
import os
import re
import shutil
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main/{}.json"

# Candidate key names in YOUR file -> which source field feeds them.
# Add to these lists if your schema uses a name that isn't here.
FIELD_ALIASES = {
    "number":  ["section", "section_number", "section_no", "sec", "number", "id"],
    "title":   ["title", "section_title", "heading", "marginal_note", "name"],
    "text":    ["text", "content", "body", "section_desc", "description", "desc"],
    "act":     ["act", "act_name", "statute", "source_act"],
    "chapter": ["chapter", "chapter_no", "chapter_number"],
    "chapter_title": ["chapter_title", "chapter_name"],
}

ACT_LABELS = {
    "ipc": "Indian Penal Code, 1860",
    "crpc": "Code of Criminal Procedure, 1973",
    "cpc": "Code of Civil Procedure, 1908",
    "iea": "Indian Evidence Act, 1872",
    "nia": "Negotiable Instruments Act, 1881",
    "hma": "Hindu Marriage Act, 1955",
    "ida": "Indian Divorce Act, 1869",
    "MVA": "Motor Vehicles Act, 1988",
}


def fetch(act):
    url = RAW.format(act)
    print(f"  fetching {url}")
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def load_existing(path):
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    records = data if isinstance(data, list) else next(
        (v for v in data.values() if isinstance(v, list)), None
    )
    if not records:
        return data, None
    return data, records[0]


def build_mapping(sample_record):
    """Map each key in your existing schema to a source field name."""
    if not sample_record:
        return None
    mapping = {}
    for your_key in sample_record:
        lowered = your_key.lower()
        for canonical, aliases in FIELD_ALIASES.items():
            if lowered in aliases:
                mapping[your_key] = canonical
                break
        else:
            mapping[your_key] = None  # unknown -> carry the old default value
    return mapping


def convert(src_rows, mapping, sample_record, act_label):
    out = []
    for row in src_rows:
        values = {
            "number": str(row.get("Section", "")).strip(),
            "title": (row.get("section_title") or "").strip(),
            "text": (row.get("section_desc") or "").strip(),
            "act": act_label,
            "chapter": row.get("chapter"),
            "chapter_title": (row.get("chapter_title") or "").strip(),
        }
        if mapping is None:  # no existing file to imitate
            out.append({
                "act": values["act"],
                "section": values["number"],
                "title": values["title"],
                "text": values["text"],
                "chapter": values["chapter"],
                "chapter_title": values["chapter_title"],
            })
            continue
        rec = {}
        for your_key, canonical in mapping.items():
            if canonical:
                rec[your_key] = values[canonical]
            else:
                # key we don't recognise: reuse whatever your old records held,
                # so nothing downstream sees a missing field.
                rec[your_key] = sample_record.get(your_key)
        out.append(rec)
    return out


def verify(records, mapping, repeal_path):
    num_key = next((k for k, v in (mapping or {}).items() if v == "number"), "section")
    text_key = next((k for k, v in (mapping or {}).items() if v == "text"), "text")
    nums = [str(r.get(num_key, "")).strip() for r in records]

    print(f"\n  sections: {len(records)}   unique: {len(set(nums))}")
    print(f"  lettered: {sum(1 for n in nums if re.search(r'[A-Za-z]', n))}")
    empty = [n for n, r in zip(nums, records) if not str(r.get(text_key, '')).strip()]
    if empty:
        print(f"  WARNING empty body text: {empty}")

    for probe in ["302", "420", "376", "124A", "498A", "511"]:
        print(f"  {probe:<6} {'ok' if probe in nums else 'MISSING'}")

    if os.path.exists(repeal_path):
        with open(repeal_path, encoding="utf-8") as f:
            rm = json.load(f)
        keys = rm.keys() if isinstance(rm, dict) else [
            str(x.get("section") or x.get("from") or "") for x in rm
        ]
        ipc_keys = {re.sub(r"^\s*(IPC|ipc)[\s_:-]*", "", str(k)).strip() for k in keys}
        matched = ipc_keys & set(nums)
        print(f"\n  repeal_map entries: {len(ipc_keys)}  |  resolvable against new ipc.json: {len(matched)}")
        missing = sorted(ipc_keys - set(nums))[:15]
        if missing:
            print(f"  repeal_map keys with no section: {missing}")
            print("  (if this list is large, your repeal_map key format differs — "
                  "check prefixes before trusting the badge)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--act", default="ipc", choices=list(ACT_LABELS))
    ap.add_argument("--data-dir", default="backend/data")
    ap.add_argument("--write", action="store_true", help="actually overwrite (default is dry run)")
    args = ap.parse_args()

    target = os.path.join(args.data_dir, f"{args.act}.json")
    repeal = os.path.join(args.data_dir, "repeal_map.json")

    print(f"target: {target}")
    container, sample = load_existing(target)
    if sample:
        print(f"  existing schema keys: {list(sample.keys())}")
    else:
        print("  no existing file (or empty) — writing a default schema")

    mapping = build_mapping(sample)
    if mapping:
        unknown = [k for k, v in mapping.items() if v is None]
        print(f"  field mapping: {mapping}")
        if unknown:
            print(f"  NOTE unmapped keys {unknown} will reuse old values verbatim. "
                  f"Add them to FIELD_ALIASES if they should be populated.")

    src = fetch(args.act)
    print(f"  source rows: {len(src)}")

    records = convert(src, mapping, sample, ACT_LABELS[args.act])
    verify(records, mapping, repeal)

    if not args.write:
        print("\nDRY RUN — nothing written. Re-run with --write once the mapping above looks right.")
        return

    if os.path.exists(target):
        shutil.copy2(target, target + ".bak")
        print(f"\n  backed up -> {target}.bak")

    payload = records
    if isinstance(container, dict):
        # your file wraps the list in an object — keep that shape
        key = next(k for k, v in container.items() if isinstance(v, list))
        container[key] = records
        payload = container

    os.makedirs(args.data_dir, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  wrote {target}")
        print("\nNext: restart the API. Do NOT run ingest_bare_acts — it regenerates "
          "ipc.json from Hugging Face and will overwrite this file with 12 sections.")

if __name__ == "__main__":
    sys.exit(main())