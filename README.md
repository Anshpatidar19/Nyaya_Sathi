<div align="center">

<h1>न्या &nbsp; Nyaya Sathi</h1>

<p><strong>Citation-traceable AI for Indian legal research.</strong><br>
Ask in plain language, get a plain-language answer — with every claim linked back to a verifiable statute section or judgment.</p>

<p>
<img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
<img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI">
<img src="https://img.shields.io/badge/React-Vite-646CFF?logo=vite&logoColor=white" alt="React + Vite">
<img src="https://img.shields.io/badge/Postgres-Supabase-3ECF8E?logo=supabase&logoColor=white" alt="Supabase Postgres">
<img src="https://img.shields.io/badge/LLM-Gemini-4285F4?logo=google&logoColor=white" alt="Gemini">
<img src="https://img.shields.io/badge/Vectors-Pinecone-000000" alt="Pinecone">
</p>

<p>
<strong>3,770</strong> statute sections indexed &nbsp;·&nbsp;
<strong>24</strong> bare acts &nbsp;·&nbsp;
<strong>6</strong> languages &nbsp;·&nbsp;
<strong>84</strong> graded retrieval evals
</p>

<!-- TODO(Sawan): add a screenshot or a short GIF of the Ask view here.
     A README with one good screenshot converts far better than one without.
     <img src="docs/screenshot-ask.png" alt="The Ask view" width="820"> -->

</div>

---

## The problem this solves

General-purpose chatbots answer Indian legal questions fluently and cite sections that do not exist. The failure is specific: a hallucinated section number reads exactly like a real one, so the person least able to verify it is the person most likely to act on it.

Nyaya Sathi is built so that a citation cannot be invented. Source cards are constructed from retrieved chunk metadata, never from model-generated text, and a post-generation gate rejects any citation marker that does not resolve to a retrieved chunk. Answer confidence is computed from retrieval signals rather than by asking the model how sure it is.

Two audiences, one retrieval layer:

- **Members** — citizens asking about a notice, an eviction, a bounced cheque. Plain-language answer, cited sources, and an ordered next step.
- **Advocates** — research, drafting, argument construction, and a case-file workspace.

---

## What's built

<table>
<tr><td width="50%" valign="top">

**Research & answering**
- Two-tier retrieval: local BM25 over 3,770 statute sections + Indian Kanoon for case law
- Context-aware chunking, benchmarked across five variants
- Routing gate that decides when case law is worth an API call
- Citation-weighted ranking, court scoping, circuit breaker on the dead Kanoon endpoint
- Grounding assessment derived from retrieval signals, not model self-rating
- Repealed-section flagging with successor pointers (IPC→BNS, CrPC→BNSS, IEA→BSA)

</td><td width="50%" valign="top">

**Workspace**
- Four modes: Ask, Draft, Review, Arguments
- Matters — case files with Overview, Timeline, Notes, Documents, Research, plus a hearing calendar
- Document upload with a scope classifier that rejects non-legal files
- Advocate directory, connection requests, private threads, notifications
- Six languages — English, Hindi, Marathi, Tamil, Telugu, Kannada
- Role-gated UI, route guards, dark theme

</td></tr>
</table>

**Multilingual, done carefully.** Retrieval and source text stay in English; only explanation text is translated. Act names and section numbers are never translated — they are legal identifiers, not natural language. Translations are cached per turn per language in `answer_translations`.

---

## Architecture

```
                        ┌──────────────────────────────┐
   React / Vite ───────▶│  FastAPI                     │
   (no DB client)       │  auth · ask · matters · net  │
                        └──────┬───────────────┬───────┘
                               │               │
              ┌────────────────┴──┐         ┌──┴─────────────────┐
              │  Retrieval        │         │  Supabase          │
              │  ┌──────────────┐ │         │  Postgres (14 tbl) │
              │  │ BM25 (local) │ │         │  Auth · Storage    │
              │  │ 3,770 secs   │ │         └────────────────────┘
              │  ├──────────────┤ │
              │  │ Pinecone     │ │  ┌────────────────────┐
              │  │ dense fallbk │ │  │  Gemini            │
              │  ├──────────────┤ │  │  thinking model,   │
              │  │ Kanoon API   │ │  │  budgeted          │
              │  └──────────────┘ │  └────────────────────┘
              └───────────────────┘
```

The frontend holds no Supabase client. Every database and storage call goes through FastAPI, which means authorisation lives in one place (Python) rather than being split between application code and RLS policies. RLS is enabled on all 14 tables with no policies: the backend connects as the table owner and bypasses row security, while the public anon key gets nothing through PostgREST.

<details>
<summary><strong>Data model — 14 tables</strong></summary>

| Group | Tables |
|---|---|
| Identity | `users`, `advocate_profiles` |
| Research | `conversations`, `query_logs`, `answer_translations` |
| Case files | `matters`, `matter_events`, `matter_notes`, `documents` |
| Network | `connections`, `chat_threads`, `chat_participants`, `chat_messages`, `notifications` |

`users.auth_id` links to Supabase Auth by UUID. Profile rows are created by `ensure_profile()` in application code on the first authenticated request — there is no on-signup database trigger, which keeps signup behaviour readable in one file.

</details>

<details>
<summary><strong>Retrieval evaluation</strong></summary>

`backend/data/eval_questions.json` holds 84 graded questions across two tiers — 74 semantic (does the right section come back for a layman phrasing) and 10 citation (does an exact section reference resolve). Measured on Recall@3, Recall@1 and MRR.

```bash
python -m app.check_retrieval
```

Retrieval quality, not pipeline plumbing, is the hard part of this project. The eval set exists so that changes can be shown to help rather than assumed to.

</details>

<details>
<summary><strong>Statute corpus</strong></summary>

24 bare acts, 3,770 sections, as flat JSON in `backend/data/`. Sourced from the `mratanusarkar/Indian-Laws` dataset, with a parser that handles colonial-era acts using newline-only title separators.

Complete: BNS (358), BNSS (531), BSA (170), IPC (575), CrPC (484), Constitution (455), MV Act (217), NI Act (142), CPA 2019 (107), IT Act (94), Arbitration (86), TPA (85), Contract (75), plus family, POCSO, SC/ST, PWDVA, RTI and others.

Known gaps: CPC covers sections but not the Orders and Rules; the Limitation Act covers sections but not the Schedule.

</details>

---

## Running it

**Requires** Python 3.12+, Node 18+, and a Supabase project.

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # then fill it in — see the table below
uvicorn app.main:app --reload --port 8000
```

API at `http://127.0.0.1:8000`, interactive docs at `/docs`. Tables are created on startup via SQLAlchemy `create_all`, and the storage bucket is created on first boot.

### 2. Ingest the statute corpus

Skip this if `backend/data/*.json` came with the clone.

```bash
python -m app.ingest_bare_acts      # build the local BM25 corpus
python -m app.ingest_hf_acts        # optional: re-ingest from Hugging Face
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://127.0.0.1:5173`, talking to `http://127.0.0.1:8000` by default. Override with `VITE_API_URL` in `frontend/.env`.

### 4. Demo accounts

```bash
python -m app.seed_demo             # 34 advocates + 2 members, pre-confirmed
python -m app.seed_demo --list      # reprint the credentials
python -m app.seed_demo --reset     # remove them all
```

Accounts are created through the Auth admin API with `email_confirm=true`, so no confirmation emails are sent, at the IANA-reserved `example.com` domain. Every one is flagged `is_demo=true`, so `--reset` can never touch a real account.

---

## Configuration

Required:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Supabase session pooler URI, `postgresql+psycopg2://…:5432/postgres` |
| `SUPABASE_URL` | Project URL, used for Storage and Auth |
| `SUPABASE_SERVICE_KEY` | Server-side key. Bypasses RLS — backend only, never the frontend |
| `SUPABASE_ANON_KEY` | Used for signup, login and password recovery |
| `GEMINI_API_KEY` | Answer synthesis |
| `JWT_SECRET_KEY` | Legacy local auth path |

Strongly recommended:

| Variable | Why |
|---|---|
| `SUPABASE_JWT_SECRET` | Verifies tokens locally. Without it, every authenticated request makes a network round trip to Auth |
| `SITE_URL` | Must match the Auth redirect allow-list, or reset and confirmation links break |
| `INDIAN_KANOON_API_TOKEN` | Case law retrieval. Statute answers work without it |

Optional: `PINECONE_*` and `DENSE_FALLBACK` for dense-retrieval fallback, `GEMINI_MODEL`, `SUPABASE_BUCKET`, `SUPABASE_AVATAR_BUCKET`, `DEMO_PASSWORD`, `DEMO_EMAIL_DOMAIN`, `EMBED_BATCH_SIZE`, `EMBED_PAUSE_SECONDS`.

> `.env.example` is currently missing four variables that `config.py` reads: `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_AVATAR_BUCKET` and `SITE_URL`. Worth adding before anyone else clones this.

**Never commit `.env`.** It is gitignored, but `git add .` has nearly leaked keys here more than once. Rotate anything that gets exposed.

---

## Engineering notes

Things that only became clear by hitting them.

**Thinking models need an explicit token budget.** Without one, the model spends most of its output allowance on reasoning and returns a truncated answer. `gemini.py` sets the budget explicitly, filters thought parts out of the JSON path, and repairs truncated JSON.

**Thought parts contaminate structured output.** The thinking variant returns reasoning as additional content parts. Concatenating everything and parsing it as JSON fails intermittently and confusingly. `generate_json()` routes all structured output through one place with `finishReason` propagated.

**Citation enforcement has to be architectural.** Source cards are built from verified chunk metadata. A validation gate after generation drops any marker that doesn't resolve. Asking the model to cite accurately is not a control.

**Model self-rating is unreliable.** Answer confidence comes from retrieval signals — score distribution and coverage — not from the model's own assessment.

**Scope gating controls both cost and quality.** The routing gate decides when a Kanoon call is worth making; the scope classifier rejects non-legal uploads. Both came out of real cost and quality problems.

**Performance work that mattered:** token validation cached with a 60s TTL and request coalescing, a persistent HTTP client with keep-alive, GET deduplication on the frontend, batched Matters queries, and eager-loaded conversation turns.

---

## Compliance

- **Advocates Act, 1961** — every surface states this is legal information, not legal advice, and no advocate–client relationship is created.
- **DPDP Act, 2023** — uploads are private by default and served through short-lived signed URLs, never public links.
- **BCI Rule 36** — directory listing is opt-in (`is_listed`). Only the fields the 2008 amendment permits are shown: name, qualification, areas of practice, contact. No ratings, no fees, no purchasable ranking. Sorting is by experience and name.

Repealed sections are flagged with successor pointers, because a confidently correct answer about a repealed provision is still a wrong answer.

---

## Roadmap

- Limitation Act Schedule and CPC Orders — the parts practitioners actually cite
- Deadline calculator writing computed dates into a matter's timeline
- Sending a cited research answer into an advocate thread, joining `query_logs` to `chat_messages`
- Growing the eval set past 84, so single-question noise stops looking like progress
- Widening the repeal map beyond its current 134 section-level mappings

---

## Credits

Built by **Sawan Kushwah** https://github.com/sawan-kush <!-- TODO(Sawan): replace with your real GitHub handle and URL -->
with **Ansh Patidar** ([@Anshpatidar19](https://github.com/Anshpatidar19)) on database and environment.

Statute corpus from [`mratanusarkar/Indian-Laws`](https://huggingface.co/datasets/mratanusarkar/Indian-Laws). Case law via the [Indian Kanoon API](https://api.indiankanoon.org). Repeal concordance from NCRB tables.

<div align="center">
<sub>Nyaya Sathi provides legal information, not legal advice.</sub>
</div>
