import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import {
  acceptConnection,
  cancelConnection,
  fetchConnections,
  fetchIncomingRequests,
  fetchNotifications,
  fetchSentRequests,
  markAllNotificationsRead,
  rejectConnection,
} from '../networkApi';
import { NetworkPage, refreshBadges, useNetworkBadges } from '../components/AppShell';
import {
  Avatar,
  ConfirmDialog,
  DemoBadge,
  Disclaimer,
  Empty,
  Skeleton,
  dayLabel,
  locationOf,
  timeAgo,
} from '../components/NetworkBits';

/* One page, two roles - but NOT the same tabs.

   The tab set used to be identical for both, which left a permanently empty
   tab on each side:

     * "Requests" for a client. The backend only creates a request whose
       receiver is an advocate, so nobody can ever ask a client to connect -
       the tab could only ever read 0.
     * "Sent" for an advocate. Advocates have no directory to search (see
       AppNav), so they have nothing to send.

   An always-empty tab reads as a broken feature rather than an empty one,
   so each role now gets only the tabs that can hold something. */

/* Tab labels deliberately do NOT repeat the page title or the nav item.
   "My Advocates" was the sidebar entry, the heading AND a tab, which made
   the tab row look like it did nothing. */

const ADVOCATE_TABS = [
  { id: 'requests', label: 'Requests' },
  { id: 'connections', label: 'Clients' },
  { id: 'notifications', label: 'Notifications' },
];

const CLIENT_TABS = [
  { id: 'connections', label: 'Connected' },
  { id: 'sent', label: 'Pending' },
  { id: 'notifications', label: 'Notifications' },
];

export default function Network() {
  const { token, isAdvocate, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { counts } = useNetworkBadges();

  const tabs = isAdvocate ? ADVOCATE_TABS : CLIENT_TABS;
  // An advocate lands on what needs them; a client lands on who they have.
  const defaultTab = isAdvocate ? 'requests' : 'connections';
  const requested = params.get('tab');
  const tab = tabs.some((t) => t.id === requested) ? requested : defaultTab;

  const [requests, setRequests] = useState([]);
  const [connections, setConnections] = useState([]);
  const [sent, setSent] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [confirm, setConfirm] = useState(null);

  // Which notifications were unread when the tab was opened. They are marked
  // read immediately so the bell clears, but they keep their unread accent
  // for this visit - otherwise clearing the badge would also erase the only
  // signal showing WHICH ones were new.
  const wasUnread = useRef(new Set());
  const marked = useRef(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // In parallel, and only the calls this role can have results for.
      // A client fetching incoming requests was a guaranteed-empty round
      // trip on every page load.
      const [c, n, r, s] = await Promise.all([
        fetchConnections(token),
        fetchNotifications(token),
        isAdvocate ? fetchIncomingRequests(token) : Promise.resolve([]),
        isAdvocate ? Promise.resolve([]) : fetchSentRequests(token),
      ]);
      setConnections(c);
      setNotifications(n);
      setRequests(r);
      setSent(s);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [token, isAdvocate]);

  useEffect(() => {
    if (authLoading || !token) return;
    load();
  }, [authLoading, token, load]);

  /* Reading the list is what marks it read.
     Previously the only things that cleared a notification were clicking it
     or the "Mark all as read" button - so opening the bell showed the list
     and left the red dot exactly where it was. */
  useEffect(() => {
    if (tab !== 'notifications' || loading || !token) return;
    const unread = notifications.filter(
      (n) => !n.is_read && !marked.current.has(n.id)
    );
    if (!unread.length) return;

    unread.forEach((n) => {
      wasUnread.current.add(n.id);
      marked.current.add(n.id);
    });

    markAllNotificationsRead(token)
      .then(() => {
        setNotifications((rows) => rows.map((n) => ({ ...n, is_read: true })));
        refreshBadges();
      })
      .catch(() => {
        // Leave them unread rather than pretending. The next visit retries.
        unread.forEach((n) => marked.current.delete(n.id));
      });
  }, [tab, loading, notifications, token]);

  function setTab(next) {
    const p = {};
    if (next !== defaultTab) p.tab = next;
    setParams(p, { replace: true });
  }

  function openNotification(n) {
    // Go where the notification is ABOUT, not just mark it read.
    if (n.related_thread_id) return navigate(`/messages/${n.related_thread_id}`);
    if (n.type === 'connection_request') return setTab('requests');
    // A declined request: the directory is the useful next step, not the
    // profile of the advocate who just said no. And /advocates/:id would
    // 404 whenever the related user is a client rather than an advocate,
    // which is exactly the case for every notification an advocate gets.
    if (!isAdvocate && n.type === 'connection_rejected') return navigate('/advocates');
    if (!isAdvocate && n.related_user_id) {
      return navigate(`/advocates/${n.related_user_id}`);
    }
    return setTab('notifications');
  }

  async function act(fn, id) {
    setBusyId(id);
    try {
      await fn(token, id);
      await load();
      refreshBadges();
      setConfirm(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  const tabCounts = useMemo(
    () => ({
      requests: requests.length,
      connections: connections.length,
      sent: sent.length,
      notifications: counts.notifications,
    }),
    [requests, connections, sent, counts.notifications]
  );

  return (
    <NetworkPage active="network">
      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          body={confirm.body}
          confirmLabel={confirm.label}
          danger
          busy={busyId === confirm.id}
          onClose={() => setConfirm(null)}
          onConfirm={() => act(confirm.fn, confirm.id)}
        />
      )}

      <div className="page-scroll">
        <div className="page-wrap">
          <div className="page-head">
            <div>
              <h1>{isAdvocate ? 'Clients & Requests' : 'My Advocates'}</h1>
              <p className="page-sub">
                {isAdvocate
                  ? 'People asking to connect, and the clients you are already talking to.'
                  : 'Advocates you have connected with, and requests still waiting for a reply.'}
              </p>
            </div>
          </div>

          <div className="seg-tabs">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`seg-tab ${tab === t.id ? 'active' : ''}`}
                onClick={() => setTab(t.id)}
              >
                {t.label}
                {/* A "0" pill on every tab is noise. Only a real number
                    earns the badge. */}
                {tabCounts[t.id] > 0 && (
                  <span className="seg-count">{tabCounts[t.id]}</span>
                )}
              </button>
            ))}
          </div>

          {error && <div className="form-error">{error}</div>}

          {loading ? (
            <Skeleton rows={3} />
          ) : tab === 'requests' ? (
            requests.length === 0 ? (
              <Empty
                icon="inbox"
                title="No pending requests"
                text="When someone asks to connect, it appears here and in the bell."
              />
            ) : (
              <>
                <Disclaimer compact />
                <div className="nx-list">
                  {requests.map((r) => (
                    <article className="nx-req-card" key={r.id}>
                      <Avatar user={r.other} size={48} />
                      <div className="nx-req-body">
                        <div className="nx-adv-name-row">
                          <h3>{r.other.name}</h3>
                          <DemoBadge user={r.other} />
                        </div>
                        <div className="nx-adv-meta">
                          {locationOf(r.other) || 'Location not set'}
                          <span className="nx-dot">&middot;</span>
                          {timeAgo(r.created_at)}
                        </div>
                        {r.intro_message && (
                          <blockquote className="nx-quote">
                            {r.intro_message}
                          </blockquote>
                        )}
                      </div>
                      <div className="nx-req-actions">
                        <button
                          type="button"
                          className="btn btn-primary sm"
                          disabled={busyId === r.id}
                          onClick={() => act(acceptConnection, r.id)}
                        >
                          {busyId === r.id ? 'Working\u2026' : 'Accept'}
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost sm danger"
                          disabled={busyId === r.id}
                          onClick={() =>
                            setConfirm({
                              id: r.id,
                              fn: rejectConnection,
                              title: `Decline ${r.other.name}?`,
                              body:
                                'They will be told the request was declined. ' +
                                'They can send a new one later.',
                              label: 'Decline',
                            })
                          }
                        >
                          Decline
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              </>
            )
          ) : tab === 'connections' ? (
            connections.length === 0 ? (
              <Empty
                icon="users"
                title={isAdvocate ? 'No clients yet' : 'No advocates yet'}
                text={
                  isAdvocate
                    ? 'Accept a connection request and a private conversation opens automatically.'
                    : 'Search the directory and send a request to get started.'
                }
                action={isAdvocate ? null : { label: 'Find an Advocate', to: '/advocates' }}
                onAction={navigate}
              />
            ) : (
              <div className="nx-list">
                {connections.map((c) => (
                  <article className="nx-conn-card" key={c.id}>
                    <Avatar user={c.other} size={44} />
                    <div className="nx-conn-body">
                      <div className="nx-adv-name-row">
                        <h3>{c.other.name}</h3>
                        <DemoBadge user={c.other} />
                      </div>
                      <div className="nx-adv-meta">
                        {c.other_specialization && (
                          <>
                            {c.other_specialization}
                            <span className="nx-dot">&middot;</span>
                          </>
                        )}
                        {locationOf(c.other) || 'Location not set'}
                        <span className="nx-dot">&middot;</span>
                        connected {timeAgo(c.updated_at || c.created_at)}
                      </div>
                    </div>
                    <div className="nx-conn-actions">
                      {/* Only an advocate has a profile to open. A client's
                          name is not a link to anywhere. */}
                      {c.other.role === 'advocate' && (
                        <button
                          type="button"
                          className="btn btn-ghost sm"
                          onClick={() => navigate(`/advocates/${c.other.id}`)}
                        >
                          Profile
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-primary sm"
                        onClick={() =>
                          navigate(c.thread_id ? `/messages/${c.thread_id}` : '/messages')
                        }
                      >
                        Message
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )
          ) : tab === 'sent' ? (
            sent.length === 0 ? (
              <Empty
                icon="clock"
                title="Nothing pending"
                text="Requests waiting for a reply will show up here."
                action={{ label: 'Find an Advocate', to: '/advocates' }}
                onAction={navigate}
              />
            ) : (
              <div className="nx-list">
                {sent.map((s) => (
                  <article className="nx-conn-card" key={s.id}>
                    <Avatar user={s.other} size={44} />
                    <div className="nx-conn-body">
                      <div className="nx-adv-name-row">
                        <h3>{s.other.name}</h3>
                        <span className="nx-badge nx-status nx-status-pending">
                          Awaiting reply
                        </span>
                      </div>
                      <div className="nx-adv-meta">
                        {s.other_specialization && (
                          <>
                            {s.other_specialization}
                            <span className="nx-dot">&middot;</span>
                          </>
                        )}
                        sent {timeAgo(s.created_at)}
                      </div>
                      {s.intro_message && (
                        <blockquote className="nx-quote">
                          {s.intro_message}
                        </blockquote>
                      )}
                    </div>
                    <div className="nx-conn-actions">
                      <button
                        type="button"
                        className="btn btn-ghost sm"
                        onClick={() => navigate(`/advocates/${s.other.id}`)}
                      >
                        Profile
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost sm danger"
                        disabled={busyId === s.id}
                        onClick={() =>
                          setConfirm({
                            id: s.id,
                            fn: cancelConnection,
                            title: 'Withdraw this request?',
                            body: `${s.other.name} will no longer see it. You can send a new one later.`,
                            label: 'Withdraw',
                          })
                        }
                      >
                        Withdraw
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )
          ) : notifications.length === 0 ? (
            <Empty
              icon="bell"
              title="Nothing yet"
              text="Requests, replies and new messages show up here."
            />
          ) : (
            <div className="nx-list">
              {notifications.map((n) => (
                <button
                  type="button"
                  className={`nx-notif ${wasUnread.current.has(n.id) ? 'unread' : ''}`}
                  key={n.id}
                  onClick={() => openNotification(n)}
                >
                  <span className={`nx-notif-dot t-${n.type}`} />
                  <span className="nx-notif-body">
                    <span className="nx-notif-title">{n.title}</span>
                    {n.body && <span className="nx-notif-text">{n.body}</span>}
                    <span className="nx-notif-time">
                      {dayLabel(n.created_at)} &middot; {timeAgo(n.created_at)}
                    </span>
                  </span>
                  <span className="nx-notif-chev" aria-hidden="true">&rsaquo;</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </NetworkPage>
  );
}