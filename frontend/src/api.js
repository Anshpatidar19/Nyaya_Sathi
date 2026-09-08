const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

/* ---------------- GET de-duplication ----------------
   The same read was firing several times per page visit: React StrictMode
   double-invokes effects in development, and an effect whose dependencies
   include `authLoading` and `isAdvocate` reruns as each of those settles.
   Six identical calls to /matters is six round trips for one screen.

   Rather than chase every effect, identical GETs are collapsed here. A
   request already in flight is shared, and its result is reused for a moment
   afterwards so a remount doesn't immediately refetch. Anything that writes
   (POST/PATCH/DELETE) is untouched, and callers can force a fresh read.  */
const GET_TTL = 1500;                 // ms a result is reused
const _inflight = new Map();          // url -> Promise
const _recent = new Map();            // url -> { at, value }

async function cachedGet(url, token, { fresh = false } = {}) {
  if (!fresh) {
    const hit = _recent.get(url);
    if (hit && Date.now() - hit.at < GET_TTL) return hit.value;

    const pending = _inflight.get(url);
    if (pending) return pending;
  }

  const p = (async () => {
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    const value = await handle(res);
    _recent.set(url, { at: Date.now(), value });
    return value;
  })();

  _inflight.set(url, p);
  try {
    return await p;
  } finally {
    _inflight.delete(url);
  }
}

/* Called after any write, so the next read doesn't serve a stale list. */
export function invalidateReads(prefix = '') {
  for (const key of [..._recent.keys()]) {
    if (!prefix || key.includes(prefix)) _recent.delete(key);
  }
}

async function handle(res) {
  if (!res.ok) {
    let detail = 'Something went wrong. Please try again.';
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function registerUser({ name, email, password, state, role }) {
  const res = await fetch(`${BASE_URL}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // `role` is 'user' or 'advocate'. The backend rejects anything else.
    body: JSON.stringify({ name, email, password, state, role }),
  });
  // May return { confirmation_required: true } with no token - the user has
  // to click the emailed link before they can log in.
  return handle(res);
}

export async function resendConfirmation(email) {
  const res = await fetch(`${BASE_URL}/auth/resend-confirmation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  return handle(res);
}

export async function forgotPassword(email) {
  const res = await fetch(`${BASE_URL}/auth/forgot-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  return handle(res);
}

export async function loginUser({ email, password }) {
  const body = new URLSearchParams();
  body.set('username', email);
  body.set('password', password);
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  return handle(res);
}

export async function fetchMe(token) {
  const res = await fetch(`${BASE_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return handle(res);
}

export async function askQuestion(token, { question, state, conversation_id, document_id }) {
  const res = await fetch(`${BASE_URL}/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    // conversation_id continues an existing thread; omit it to start one.
    // document_id asks the question about a previously uploaded file.
    body: JSON.stringify({ question, state, conversation_id, document_id }),
  });
  return handle(res);
}

// Streams the answer as it is written. onDelta gets each new run of body
// text, onRevised fires only when the validator replaced what was already
// shown, and the promise resolves with the finished answer (citations,
// grounding, next steps, conversation_id).
//
// fetch + ReadableStream rather than EventSource: EventSource cannot send an
// Authorization header or a POST body, and both are needed here.
export async function askQuestionStream(
  token,
  { question, state, conversation_id, document_id },
  { onDelta, onRevised } = {},
) {
  const res = await fetch(`${BASE_URL}/ask/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ question, state, conversation_id, document_id }),
  });

  if (!res.ok) {
    // The endpoint can still fail before the stream opens - a bad document
    // id, or the scope gate rejecting the upload.
    return handle(res);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let answer = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line. The last piece is kept back
    // because it may be half a frame.
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';

    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;

      let event;
      try {
        event = JSON.parse(line.slice(5).trim());
      } catch {
        continue;   // a frame we can't read is not worth failing the answer over
      }

      if (event.type === 'delta') onDelta?.(event.text);
      else if (event.type === 'revised') onRevised?.(event.body);
      else if (event.type === 'done') answer = event.answer;
      else if (event.type === 'error') throw new Error(event.message);
    }
  }

  if (!answer) throw new Error('The answer ended before it was complete.');
  return answer;
}

// Re-renders a stored answer in another language. The English original is
// never overwritten server-side - this returns a rendering of it, and the
// backend caches the result so switching back is instant.
export async function translateAnswer(token, { query_log_id, language }) {
  const res = await fetch(`${BASE_URL}/translate/answer`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ query_log_id, language }),
  });
  return handle(res);
}

// Translates a drafted document. Not cached: a draft is edited between
// requests, so a cache would either miss constantly or go stale.
export async function translateText(token, { text, language }) {
  const res = await fetch(`${BASE_URL}/translate/text`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ text, language }),
  });
  return handle(res);
}

// Uploads go through multipart/form-data, so no Content-Type header here -
// the browser sets it with the boundary token, and overriding it breaks the
// parse on the server side.
export async function uploadDocument(token, file, note) {
  const form = new FormData();
  form.append('file', file);
  if (note) form.append('note', note);

  const res = await fetch(`${BASE_URL}/documents`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  return handle(res);
}

export async function fetchArgumentSides(token) {
  return cachedGet(`${BASE_URL}/arguments/sides`, token);
}

export async function generateArguments(token, payload) {
  const res = await fetch(`${BASE_URL}/arguments`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    // { facts, document_id, side, issue, court, state }
    body: JSON.stringify(payload),
  });
  return handle(res);
}

/* ---------------- Matters (advocate) ---------------- */

const auth = (token) => ({ Authorization: `Bearer ${token}` });
const authJson = (token) => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${token}`,
});

export async function fetchMatters(token, { status, q, fresh } = {}) {
  const params = new URLSearchParams();
  if (status) params.set('status_filter', status);
  if (q) params.set('q', q);
  const qs = params.toString();
  return cachedGet(`${BASE_URL}/matters${qs ? `?${qs}` : ''}`, token, { fresh });
}

export async function fetchMatter(token, id, { fresh } = {}) {
  return cachedGet(`${BASE_URL}/matters/${id}`, token, { fresh });
}

export async function createMatter(token, payload) {
  const res = await fetch(`${BASE_URL}/matters`, {
    method: 'POST',
    headers: authJson(token),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function updateMatter(token, id, payload) {
  const res = await fetch(`${BASE_URL}/matters/${id}`, {
    method: 'PATCH',
    headers: authJson(token),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function deleteMatter(token, id) {
  const res = await fetch(`${BASE_URL}/matters/${id}`, {
    method: 'DELETE',
    headers: auth(token),
  });
  if (!res.ok) throw new Error('Could not delete that matter.');
}

export async function fetchUpcomingEvents(token, days = 90, { fresh } = {}) {
  return cachedGet(`${BASE_URL}/matters/upcoming?days=${days}`, token, { fresh });
}

export async function createMatterEvent(token, matterId, payload) {
  const res = await fetch(`${BASE_URL}/matters/${matterId}/events`, {
    method: 'POST',
    headers: authJson(token),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function updateMatterEvent(token, matterId, eventId, payload) {
  const res = await fetch(`${BASE_URL}/matters/${matterId}/events/${eventId}`, {
    method: 'PATCH',
    headers: authJson(token),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function deleteMatterEvent(token, matterId, eventId) {
  const res = await fetch(`${BASE_URL}/matters/${matterId}/events/${eventId}`, {
    method: 'DELETE',
    headers: auth(token),
  });
  if (!res.ok) throw new Error('Could not delete that entry.');
}

export async function createMatterNote(token, matterId, payload) {
  const res = await fetch(`${BASE_URL}/matters/${matterId}/notes`, {
    method: 'POST',
    headers: authJson(token),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function updateMatterNote(token, matterId, noteId, payload) {
  const res = await fetch(`${BASE_URL}/matters/${matterId}/notes/${noteId}`, {
    method: 'PATCH',
    headers: authJson(token),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function deleteMatterNote(token, matterId, noteId) {
  const res = await fetch(`${BASE_URL}/matters/${matterId}/notes/${noteId}`, {
    method: 'DELETE',
    headers: auth(token),
  });
  if (!res.ok) throw new Error('Could not delete that note.');
}

export async function deleteMatterDocument(token, matterId, docId) {
  const res = await fetch(`${BASE_URL}/matters/${matterId}/documents/${docId}`, {
    method: 'DELETE',
    headers: auth(token),
  });
  if (!res.ok) throw new Error('Could not delete that document.');
}

// Uploads straight into a matter reuse POST /documents with a matter_id part.
export async function uploadMatterDocument(token, matterId, file, note) {
  const form = new FormData();
  form.append('file', file);
  form.append('matter_id', String(matterId));
  if (note) form.append('note', note);

  const res = await fetch(`${BASE_URL}/documents`, {
    method: 'POST',
    headers: auth(token),
    body: form,
  });
  return handle(res);
}

export async function fetchDocumentUrl(token, docId) {
  const res = await fetch(`${BASE_URL}/documents/${docId}/url`, {
    headers: auth(token),
  });
  return handle(res);
}

export async function resetPassword({ access_token, new_password }) {
  const res = await fetch(`${BASE_URL}/auth/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ access_token, new_password }),
  });
  return handle(res);
}

export async function fetchConversations(token, { fresh } = {}) {
  return cachedGet(`${BASE_URL}/conversations`, token, { fresh });
}

export async function fetchConversation(token, id) {
  const res = await fetch(`${BASE_URL}/conversations/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return handle(res);
}

export async function deleteConversation(token, id) {
  const res = await fetch(`${BASE_URL}/conversations/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error('Could not delete that conversation.');
}

export async function fetchHistory(token) {
  const res = await fetch(`${BASE_URL}/ask/history`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return handle(res);
}

export async function fetchDraftTypes(token) {
  return cachedGet(`${BASE_URL}/draft/types`, token);
}

export async function createDraft(token, { doc_type, instructions, details }) {
  const res = await fetch(`${BASE_URL}/draft`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ doc_type, instructions, details }),
  });
  return handle(res);
}

export async function reviewDocument(token, { document_text, document_id, doc_type, context }) {
  const res = await fetch(`${BASE_URL}/review`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ document_text, document_id, doc_type, context }),
  });
  return handle(res);
}