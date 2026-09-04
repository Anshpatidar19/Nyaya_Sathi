"""One-off: show what /docfragment/ actually returns, so the parser can match it.

Run from backend/:
    python -m app.probe_fragment

Costs 1 Kanoon call.
"""

import asyncio
import json

from . import kanoon

DOCID = "1769219"          # from your logs
QUERY = "landmark judgments domestic violence india"


async def main():
    data = await kanoon._post(f"/docfragment/{DOCID}/", {"formInput": QUERY})

    print("TOP-LEVEL KEYS:", list(data.keys()))
    print()
    for k, v in data.items():
        kind = type(v).__name__
        if isinstance(v, str):
            print(f"  {k}  ({kind}, {len(v)} chars)")
            print(f"      {v[:300]!r}")
        elif isinstance(v, list):
            print(f"  {k}  ({kind}, {len(v)} items)")
            if v:
                print(f"      first item type: {type(v[0]).__name__}")
                print(f"      {str(v[0])[:300]!r}")
        elif isinstance(v, dict):
            print(f"  {k}  ({kind}, keys={list(v.keys())})")
        else:
            print(f"  {k}  ({kind}) = {v!r}")
        print()

    print("=" * 60)
    print("RAW (first 1500 chars):")
    print(json.dumps(data, indent=1)[:1500])


if __name__ == "__main__":
    asyncio.run(main())