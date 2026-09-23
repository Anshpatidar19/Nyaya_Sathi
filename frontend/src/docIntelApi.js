/* API layer for AI document intelligence inside a chat thread.

   Its own module rather than a section of networkApi.js for one reason:
   networkApi.js caches reads, and every call here is a POST that costs a
   Gemini round trip. Caching that correctly belongs on the server, where
   the extraction actually lives (see doc_intel.py) - a second cache in
   front of it would only make "re-run this" harder to reason about.

   Every chat route is scoped to a thread AND a document, so a document id
   on its own opens nothing. The one exception, analyzeOwnDocument, only
   opens documents the caller uploaded themselves. */

const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

const authJson = (token) => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${token}`,
});

async function handle(res) {
  if (!res.ok) {
    let detail = 'Something went wrong. Please try again.';
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      /* non-JSON error body */
    }
    const err = new Error(
      typeof detail === 'string' ? detail : 'That request was not accepted.'
    );
    err.status = res.status;
    // Same meaning as in api.js: a 422 is a refusal (out-of-scope file),
    // not a blip, so the Ask page shows it in the thread instead of
    // offering a retry that can only be refused again. The chat ignores it.
    err.terminal = res.status === 422;
    throw err;
  }
  return res.json();
}

function docUrl(threadId, documentId, action, params) {
  const qs = new URLSearchParams(params || {}).toString();
  return (
    `${BASE_URL}/network/threads/${threadId}/documents/${documentId}/${action}` +
    (qs ? `?${qs}` : '')
  );
}

/* Structured extraction: parties, dates, sections, clauses, amounts, claims,
   key facts, summary, and the gaps the questions are built from. `force`
   re-runs it instead of serving the server's cached extraction. */
export async function analyzeDocument(token, threadId, documentId, { force } = {}) {
  const res = await fetch(
    docUrl(threadId, documentId, 'analyze', force ? { force: 'true' } : null),
    { method: 'POST', headers: authJson(token) }
  );
  return handle(res);
}

/* The same extraction, for one of the advocate's OWN uploads (the Ask
   page's attach button). Same response shape as analyzeDocument, so both
   surfaces render it with the same component; only the route - and so the
   ownership check - differs. */
export async function analyzeOwnDocument(token, documentId, { force } = {}) {
  const qs = force ? '?force=true' : '';
  const res = await fetch(`${BASE_URL}/documents/${documentId}/analyze${qs}`, {
    method: 'POST',
    headers: authJson(token),
  });
  return handle(res);
}

/* What the advocate still has to ask the client. The server reads the
   thread itself for context, so nothing about the conversation is sent
   from here - it already has it, and sending it again would only risk the
   two disagreeing. */
export async function suggestQuestions(token, threadId, documentId, { force } = {}) {
  const res = await fetch(
    docUrl(threadId, documentId, 'questions', force ? { force: 'true' } : null),
    { method: 'POST', headers: authJson(token) }
  );
  return handle(res);
}

/* Q&A scoped to one document. `history` is the document thread so far -
   nothing is stored server-side, so it comes back up with every turn. */
export async function askAboutDocument(
  token,
  threadId,
  documentId,
  { question, history = [] }
) {
  const res = await fetch(docUrl(threadId, documentId, 'ask'), {
    method: 'POST',
    headers: authJson(token),
    body: JSON.stringify({
      question,
      // Only the last few turns matter and the server trims anyway; sending
      // a long tail just makes the request bigger.
      history: history.slice(-6).map((t) => ({ role: t.role, content: t.content })),
    }),
  });
  return handle(res);
}