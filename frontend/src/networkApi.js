/* API layer for the advocate network: directory, connections, chat,
   notifications and profile.

   Separate from api.js, and it used to carry no cache at all. The reasoning
   was sound but too broad: api.js collapses identical GETs for 1.5 seconds,
   which is right for the AI surface but fatal for chat, because polling the
   same URL is the whole point there and a cache would swallow every poll
   after the first.

   The fix is per-endpoint rather than per-module. Chat and badge polling are
   listed in NEVER_CACHE below and still go straight to the network every
   time. Everything else - the directory, filter options, connections, a
   profile - is read on navigation and gets a short time-to-live, because
   every one of those reads is a round trip to a database in Sydney and a
   directory page that was correct four seconds ago is still correct now.

   What this buys: navigating away and back paints from cache instead of
   waiting, and prefetch() below lets a hovered nav link start its request
   before the click lands. */

const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

/* ---------------- read cache ----------------
   Per-path TTLs. Reference data that barely changes is held longest; lists a
   user acts on are held just long enough to cover a navigation. A zero TTL
   means "always hit the network".

   Matched longest-prefix-first, so a specific rule beats a general one. */
const TTL_RULES = [
  // Polling endpoints. Caching these breaks them - see the note above.
  ['/network/unread', 0],
  ['/network/notifications', 0],
  ['/network/messages', 0],
  ['/network/threads', 0],
  // Reference data: the set of cities, states and specializations in the
  // directory. Changes when an advocate edits a profile, i.e. rarely.
  ['/network/advocates/filters', 600000],
  // Directory search results.
  ['/network/advocates', 30000],
  // Own profile, and the lists behind the network page.
  ['/network/me', 60000],
  ['/network/connections', 15000],
  ['/network/requests', 15000],
];

const DEFAULT_TTL = 15000;

function ttlFor(path) {
  let best = null;
  for (const [prefix, ttl] of TTL_RULES) {
    if (path.startsWith(prefix)) {
      if (!best || prefix.length > best[0].length) best = [prefix, ttl];
    }
  }
  return best ? best[1] : DEFAULT_TTL;
}

const _cache = new Map();      // url -> { at, value }
const _inflight = new Map();   // url -> Promise

/* Called after every write below, so the next read can't serve a list that
   the write just changed. Prefix-matched: a new connection request should
   drop the connections lists and the directory (whose rows carry connection
   state), not the whole cache. */
export function invalidateNetworkReads(prefix = '') {
  for (const key of [..._cache.keys()]) {
    if (!prefix || key.includes(prefix)) _cache.delete(key);
  }
}

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

function urlFor(path, params) {
  const qs = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v);
  });
  const q = qs.toString();
  return `${BASE_URL}${path}${q ? `?${q}` : ''}`;
}

async function get(path, token, params, { fresh = false } = {}) {
  const url = urlFor(path, params);
  const ttl = ttlFor(path);

  if (ttl > 0 && !fresh) {
    const hit = _cache.get(url);
    if (hit && Date.now() - hit.at < ttl) return hit.value;
    // An identical request already on its way is shared rather than doubled.
    // This alone removes a lot of traffic: React StrictMode double-invokes
    // effects in development, and effects keyed on `authLoading` rerun as
    // auth settles.
    const pending = _inflight.get(url);
    if (pending) return pending;
  }

  const p = (async () => {
    const res = await fetch(url, { headers: auth(token) });
    const value = await handle(res);
    if (ttl > 0) _cache.set(url, { at: Date.now(), value });
    return value;
  })();

  if (ttl > 0) {
    _inflight.set(url, p);
    try {
      return await p;
    } finally {
      _inflight.delete(url);
    }
  }
  return p;
}

/* Warm the cache for a page the user has not asked for yet.

   Called on hover and keyboard focus of the nav rail, which buys roughly the
   200-400ms between intending to click and clicking - enough, at Sydney
   latency, to turn a visible wait into an instant paint. Failures are
   swallowed on purpose: a prefetch that 401s or times out must never surface
   an error on the page the user is still looking at. */
export function prefetch(route, token) {
  if (!token) return;
  const warm = (path, params) => get(path, token, params).catch(() => {});
  if (route === '/advocates') {
    warm('/network/advocates/filters');
    warm('/network/advocates', { page: 1, per_page: 12 });
  } else if (route === '/network') {
    warm('/network/connections');
  } else if (route === '/profile') {
    warm('/network/me');
  }
}

async function send(method, path, token, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: body ? authJson(token) : auth(token),
    body: body ? JSON.stringify(body) : undefined,
  });
  const value = await handle(res);
  // Any write can change any of these lists, and a stale read straight after
  // a write is the one cache bug users always notice.
  invalidateNetworkReads('/network/');
  return value;
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

// `content` and `document_id` are both optional on the wire - a message
// needs one or the other (or both), enforced server-side.
export const postMessage = (token, threadId, { content, document_id } = {}) =>
  send('POST', `/network/threads/${threadId}/messages`, token, {
    content: content || undefined,
    document_id: document_id || undefined,
  });

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