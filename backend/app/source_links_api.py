"""GET /sources/statute/{act_key}/{section} - open the exact cited provision.

A source card whose Kanoon document id isn't known yet links here. The
handler resolves the id (once - see source_links.resolve) and 302-redirects
straight to https://indiankanoon.org/doc/<tid>/, so the user goes from the
card to the section in one click with no results list in between.

Unauthenticated on purpose: it's opened by a plain <a target="_blank">, which
carries no bearer token. It is safe to leave open because it only accepts
act/section pairs that exist in the local corpus, so the most it can ever
spend is one Kanoon search per ingested section, after which it's cached.
"""

import html
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from . import source_links, statutes

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sources"])


@router.get("/sources/statute/{act_key}/{section}")
async def open_statute_source(act_key: str, section: str):
    doc = statutes.get(act_key, section)
    if not doc:
        raise HTTPException(status_code=404, detail="Unknown act or section.")

    tid = await source_links.resolve(doc)
    if tid:
        return RedirectResponse(source_links.KANOON_DOC_URL.format(tid=tid), status_code=302)

    own = doc.get("url")
    if own and not source_links.is_search_url(own) and not source_links.is_resolver_url(own):
        return RedirectResponse(own, status_code=302)

    # Kanoon has no page for this section, or couldn't be reached. Show the
    # exact text that was cited rather than any kind of search.
    return HTMLResponse(_section_page(doc))


def _section_page(doc: dict) -> str:
    e = html.escape
    unit = doc.get("unit") or "Section"
    heading = f"{unit} {doc['section']}" + (f" — {doc['title']}" if doc.get("title") else "")
    status = "" if doc.get("current", True) else (
        f'<p class="warn">Repealed. Replaced by {e(doc.get("superseded_by") or "a successor law")}.</p>'
    )
    paras = "".join(f"<p>{e(p.strip())}</p>" for p in (doc.get("text") or "").split("\n") if p.strip())
    src = doc.get("source_url") or ""
    provenance = (
        f'Text as cited by Nyaya Sathi. Source: <a href="{e(src)}" rel="noopener">{e(src)}</a>'
        if src else "Text as cited by Nyaya Sathi from its bare-act corpus."
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(doc['act'])} — {e(unit)} {e(doc['section'])}</title>
<style>
  :root {{ --bg:#fff; --fg:#1b1f1d; --muted:#5d6661; --line:#e2e6e3; --warn:#9a3412; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#141816; --fg:#e7ece9; --muted:#9aa49f; --line:#2a302d; --warn:#fdba74; }}
  }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:17px/1.65 Georgia, "Times New Roman", serif; }}
  main {{ max-width:760px; margin:0 auto; padding:48px 24px; }}
  .act {{ font:600 13px/1.4 system-ui, sans-serif; letter-spacing:.06em;
          text-transform:uppercase; color:var(--muted); }}
  h1 {{ font-size:26px; line-height:1.3; margin:8px 0 24px; }}
  .warn {{ color:var(--warn); font:600 14px system-ui, sans-serif; }}
  footer {{ margin-top:40px; padding-top:16px; border-top:1px solid var(--line);
            font:13px/1.5 system-ui, sans-serif; color:var(--muted); }}
  a {{ color:inherit; }}
</style></head>
<body><main>
  <div class="act">{e(doc['act'])}</div>
  <h1>{e(heading)}</h1>
  {status}
  {paras}
  <footer>{provenance}</footer>
</main></body></html>"""