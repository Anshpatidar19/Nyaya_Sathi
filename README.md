# Nyaya Sathi — Legal AI Platform (Step 1 build)


Citizen-facing legal information platform: ask a question or upload a notice,
get a plain-language answer with citations to the broad act, plus a short
next-steps checklist. Built from the architecture spec — this is the first
working slice, not the full roadmap.

## What's actually wired up in this step

- **React frontend** (Vite + React Router) — the marketing/landing page,
  email+password auth (register/login), and a real "Ask" dashboard.
- **FastAPI backend** with:
  - JWT auth (register / login / me)
  - `/ask` — answers a question and logs it to the user's history
  - `/ask/history` — a user's past questions
  - `/kanoon/search` and `/kanoon/doc/{id}` — pass-through to the
    **Indian Kanoon API**, which is the dataset for this platform (case
    law + statutes, with Indian Kanoon's own structural analysis:
    facts / issues / arguments / precedent analysis / conclusion per
    paragraph, plus precedent citation sentiment — reused as-is rather
    than rebuilt).
- **SQLite** for users and query history (swap `DATABASE_URL` for Postgres
  later — no code changes needed, it's all through SQLAlchemy).

The tech stack section has been removed from the UI itself (per feedback) —
it now only shows up here and in the code, where it belongs.

## Tech stack (from the architecture doc)

| Layer | Technology |
|---|---|
| Frontend | React (Vite), React Router |
| Backend | Python, FastAPI |
| Auth | JWT (python-jose) + bcrypt password hashing |
| Orchestration (next step) | LangChain |
| Dataset / retrieval | **Indian Kanoon API** |
| Vector DB (next step) | Pinecone |
| LLM (next step) | Gemini |
| OCR (next step) | Google Cloud Vision API |
| Speech & translation (next step) | Whisper |

Right now `/ask` runs a small curated fallback answer bank so the product is
fully demoable with **zero API keys**. The Indian Kanoon retrieval path is
real and working — once you add an API token, `/kanoon/search` and
`/kanoon/doc/{id}` return live results. The last mile (feeding retrieved
judgments into an LLM synthesis + validator step, per the spec's Agentic
Orchestration Layer) is stubbed in `backend/app/reasoning.py` as
`run_live_pipeline()` — see the TODO there for what's left, that's step 2.

## Project layout

```
legal-ai-platform/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app, all routes
│   │   ├── auth.py          # JWT + password hashing
│   │   ├── models.py        # SQLAlchemy models (User, QueryLog)
│   │   ├── schemas.py       # Pydantic request/response shapes
│   │   ├── kanoon.py        # Indian Kanoon API client
│   │   ├── reasoning.py     # Synthesis + validator layer (fallback + live stub)
│   │   ├── database.py
│   │   └── config.py
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── pages/            # Landing, Login, Register, Ask
    │   ├── components/       # Navbar, Footer, ProtectedRoute
    │   ├── AuthContext.jsx   # login/register/logout, token persistence
    │   ├── api.js            # fetch wrapper for the backend
    │   └── styles.css
    ├── index.html
    └── package.json
```

## Running it locally

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env       # then fill in INDIAN_KANOON_API_TOKEN, JWT_SECRET_KEY, etc.
uvicorn app.main:app --reload --port 8000
```

The API is now at `http://127.0.0.1:8000` — interactive docs at
`http://127.0.0.1:8000/docs`.

At minimum, set `JWT_SECRET_KEY` to something random before you use this
for anything beyond your own machine. `/ask` works with no other keys set.
`/kanoon/*` needs `INDIAN_KANOON_API_TOKEN` (sign up at
https://api.indiankanoon.org).

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://127.0.0.1:5173`. It talks to the backend at
`http://127.0.0.1:8000` by default — override with a `.env` containing
`VITE_API_URL=http://your-backend-host:port` if needed.

### 3. Try it

1. Go to `/register`, create an account (name, email, 8+ char password, state).
2. You're redirected to `/ask` — try "Can my landlord evict me without notice?"
   or one of the other sample topics (bounced cheque, wrongful termination,
   faulty product refund) — these are answered by the curated fallback bank.
3. Refresh — you're still logged in (JWT persisted in localStorage). Your
   question shows up in the sidebar history, pulled from `/ask/history`.

## What's next (not built yet)

1. **Wire Gemini into `run_live_pipeline()`** in `reasoning.py` — take the
   top Indian Kanoon search results, prompt Gemini to synthesize a
   plain-language answer + next steps from them only, then run a second
   "validator" call that checks the answer doesn't say anything the
   retrieved text doesn't support.
2. **Pinecone** — once you're chunking and embedding statute/case text
   yourself (rather than relying on Kanoon's live search), index it in
   Pinecone for faster, cheaper repeated retrieval.
3. **OCR upload flow** — Google Cloud Vision for notice photos, PII
   redaction before anything reaches the LLM, matching the spec's Data
   Sanitization requirement.
4. **Whisper** — voice input, plus regional language translation in and
   out of English for the query refinement step.
5. **Multi-agent validator** — the spec's Internal Validator Agent that
   blocks/regenerates an answer if it strays from the retrieved source.

## Compliance notes carried over from the spec

- Every screen should make clear this is legal information, not legal
  advice (Advocates Act, 1961).
- Uploaded document images should be deleted after text extraction
  (DPDP Act, 2023) — not yet implemented since OCR upload isn't wired up.
- API agreements with LLM providers should confirm no training on user data.
