/* API layer for the advocate network: directory, connections, chat,
   notifications and profile.

   Deliberately separate from api.js, and deliberately WITHOUT its GET cache.

   api.js collapses identical GETs for 1.5 seconds, which is right for the
   AI surface - six components asking for /matters on one page load should
   be one request. It is wrong here: chat polls the same URL every few
   seconds on purpose, and a cache would swallow every poll after the first
   and the messages would simply never arrive.

   So every read below is a plain fetch. Nothing here is called on a hot
   path where dedup would matter. */

const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

const auth = (token) => ({ Authorization: `Bearer ${token}` });
const authJson = (token) => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${token}`,
});

async function handle(res) {
  if (res.status === 204) return null;
  if (!res.ok) {
    let detail = 'Something went wrong. Please try again.';
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      /* non-JSON error body */
    }
    const err = new Error(
      // FastAPI validation errors arrive as an array of objects, which
      // renders as "[object Object]" if passed straight to a message.
      typeof detail === 'string' ? detail : 'That input was not accepted.'
    );
    err.status = res.status;
    throw err;
  }
  return res.json();
}

async function get(path, token, params) {
  const qs = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v);
  });
  const q = qs.toString();
  const res = await fetch(`${BASE_URL}${path}${q ? `?${q}` : ''}`, {
    headers: auth(token),
  });
  return handle(res);
}

async function send(method, path, token, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: body ? authJson(token) : auth(token),
    body: body ? JSON.stringify(body) : undefined,
  });
  return handle(res);
}

/* ---------------- profile ---------------- */

export const fetchMyProfile = (token) => get('/network/me', token);

export const updateMyProfile = (token, payload) =>
  send('PATCH', '/network/me', token, payload);

export const saveAdvocateProfile = (token, payload) =>
  send('PUT', '/network/me/advocate-profile', token, payload);

export async function uploadAvatar(token, file) {
  // Multipart, so no Content-Type header - the browser has to set the
  // boundary itself. Setting it by hand produces a request the server
  // cannot parse.
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE_URL}/network/me/avatar`, {
    method: 'POST',
    headers: auth(token),
    body: form,
  });
  return handle(res);
}

export const deleteAvatar = (token) =>
  send('DELETE', '/network/me/avatar', token);

/* ---------------- directory ---------------- */

export const searchAdvocates = (token, filters) =>
  get('/network/advocates', token, filters);

export const fetchAdvocateFilters = (token) =>
  get('/network/advocates/filters', token);

export const fetchAdvocate = (token, id) =>
  get(`/network/advocates/${id}`, token);

/* ---------------- connections ---------------- */

export const sendConnectionRequest = (token, { receiver_id, intro_message }) =>
  send('POST', '/network/connections', token, { receiver_id, intro_message });

export const fetchConnections = (token) => get('/network/connections', token);

export const fetchIncomingRequests = (token) =>
  get('/network/connections/requests', token);

export const fetchSentRequests = (token) =>
  get('/network/connections/sent', token);

export const acceptConnection = (token, id) =>
  send('PATCH', `/network/connections/${id}/accept`, token);

export const rejectConnection = (token, id) =>
  send('PATCH', `/network/connections/${id}/reject`, token);

export const cancelConnection = (token, id) =>
  send('DELETE', `/network/connections/${id}`, token);

/* ---------------- chat ---------------- */

export const fetchThreads = (token) => get('/network/threads', token);

export const fetchThread = (token, id) => get(`/network/threads/${id}`, token);

/* `after_id` is the poll: pass the last id you already have and only newer
   messages come back. Omit it to load the tail of the history. */
export const fetchMessages = (token, threadId, { after_id } = {}) =>
  get(`/network/threads/${threadId}/messages`, token, { after_id });

export const postMessage = (token, threadId, content) =>
  send('POST', `/network/threads/${threadId}/messages`, token, { content });

export const markThreadRead = (token, threadId) =>
  send('POST', `/network/threads/${threadId}/read`, token);

/* ---------------- notifications ---------------- */

export const fetchNotifications = (token, { unread_only } = {}) =>
  get('/network/notifications', token, { unread_only });

export const fetchUnreadCounts = (token) => get('/network/unread', token);

export const markNotificationRead = (token, id) =>
  send('POST', `/network/notifications/${id}/read`, token);

export const markAllNotificationsRead = (token) =>
  send('POST', '/network/notifications/read-all', token);