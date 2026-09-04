const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

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

export async function registerUser({ name, email, password, state }) {
  const res = await fetch(`${BASE_URL}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, email, password, state }),
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

export async function askQuestion(token, { question, state, conversation_id }) {
  const res = await fetch(`${BASE_URL}/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    // conversation_id continues an existing thread; omit it to start one.
    body: JSON.stringify({ question, state, conversation_id }),
  });
  return handle(res);
}

export async function fetchConversations(token) {
  const res = await fetch(`${BASE_URL}/conversations`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return handle(res);
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
  const res = await fetch(`${BASE_URL}/draft/types`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return handle(res);
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