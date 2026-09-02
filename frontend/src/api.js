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

export async function askQuestion(token, { question, state }) {
  const res = await fetch(`${BASE_URL}/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ question, state }),
  });
  return handle(res);
}

export async function fetchHistory(token) {
  const res = await fetch(`${BASE_URL}/ask/history`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return handle(res);
}
