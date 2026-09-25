/* API for the Find an Advocate assistant.

   One endpoint, one POST per turn. Kept out of networkApi.js because
   everything there is a cached GET, and a recommendation must never be
   served from a cache - the directory and the user's connections both move
   underneath it. */

import { postWithProgress } from './api';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

// Same request with live progress: onStatus({stage, detail}) fires as the
// server understands the request, searches the directory and ranks the
// matches, then the same payload recommendAdvocates returns comes back.
export async function recommendAdvocatesStream(token, { message, history = [] }, { onStatus } = {}) {
  return postWithProgress(
    `${BASE_URL}/network/advocates/recommend/stream`,
    token,
    {
      message,
      history: history
        .slice(-4)
        .map((t) => ({ role: t.role, content: t.content || '' })),
    },
    { onStatus },
  );
}

export async function recommendAdvocates(token, { message, history = [] }) {
  const res = await fetch(`${BASE_URL}/network/advocates/recommend`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      message,
      // Only the recent turns matter for resolving "and in Bhopal?" against
      // the question before it. The server trims too.
      history: history
        .slice(-4)
        .map((t) => ({ role: t.role, content: t.content || '' })),
    }),
  });

  if (!res.ok) {
    let detail = 'Something went wrong. Please try again.';
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      /* non-JSON error body */
    }
    const err = new Error(
      res.status === 401
        ? 'Your session has expired. Sign in again to continue.'
        : detail
    );
    err.status = res.status;
    throw err;
  }
  return res.json();
}