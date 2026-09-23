import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { fetchMyProfile, fetchUnreadCounts, prefetch } from '../networkApi';
import { prefetchRoute } from '../api';
import ThemeToggle from './ThemeToggle';

/* The topbar and nav rail, shared so /ask, /matters and the network pages
   can't drift apart. Ask keeps its own body layout; this only owns the
   chrome around it. */

export const NavIcon = {
  ask: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 11.5a2 2 0 0 1-2 2H8l-4 3v-3H5a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    </svg>
  ),
  draft: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 2.5h7l5 5v10H4z" />
      <path d="M11 2.5v5h5" />
      <path d="M7 11h6M7 14h4" />
    </svg>
  ),
  review: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 2.5h9l3 3v12H4z" />
      <path d="m7 10 2 2 4-4.5" />
    </svg>
  ),
  argue: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 3v14M5.5 17h9" />
      <path d="M2.5 7.5l3-3.5 3 3.5a3 3 0 0 1-6 0z" />
      <path d="M11.5 7.5l3-3.5 3 3.5a3 3 0 0 1-6 0z" />
    </svg>
  ),
  matters: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M2.5 6.5a1.5 1.5 0 0 1 1.5-1.5h3l1.5 2h6a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5z" />
    </svg>
  ),
  /* --- network --- */
  find: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="9" r="5.5" />
      <path d="m13.5 13.5 3.5 3.5" />
    </svg>
  ),
  network: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <circle cx="7" cy="7" r="2.6" />
      <path d="M2.5 16.5c0-2.6 2-4.3 4.5-4.3s4.5 1.7 4.5 4.3" />
      <path d="M13.5 5.2a2.4 2.4 0 0 1 0 4.6M14.5 12.6c2 .4 3 1.9 3 3.9" />
    </svg>
  ),
  messages: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 5.5a1.5 1.5 0 0 1 1.5-1.5h11A1.5 1.5 0 0 1 17 5.5v6a1.5 1.5 0 0 1-1.5 1.5H8l-5 3.5z" />
    </svg>
  ),
  profile: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="7" r="3" />
      <path d="M4 17c0-3.1 2.7-5 6-5s6 1.9 6 5" />
    </svg>
  ),
  bell: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 3a4.5 4.5 0 0 0-4.5 4.5c0 3-1 4-1.5 4.5h12c-.5-.5-1.5-1.5-1.5-4.5A4.5 4.5 0 0 0 10 3z" />
      <path d="M8 14.5a2 2 0 0 0 4 0" />
    </svg>
  ),
  menu: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round">
      <path d="M3 6h14M3 10h14M3 14h14" />
    </svg>
  ),
  close: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round">
      <path d="M5 5l10 10M15 5L5 15" />
    </svg>
  ),
  chevron: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M7.5 4.5l6 5.5-6 5.5" />
    </svg>
  ),
};

/* ---------------------------------------------------------------------------
   Badge store
   ---------------------------------------------------------------------------
   The sidebar counts and the topbar bell want the same three numbers, and
   they are rendered as siblings by each page rather than nested - so there
   is no common parent to hold the state and a plain hook in each would mean
   two pollers per page.

   This is a one-value store with subscribers instead. Everything that needs
   the counts reads the same copy, one interval refreshes it, and a page that
   just accepted a request calls refreshBadges() so the number drops
   immediately rather than up to 30 seconds later. */

const POLL_MS = 30000;

/* A mount does not justify a request if the numbers are this fresh. Several
   components call useNetworkBadges() and each one used to trigger _load() as
   it subscribed, so a single navigation fired the poll two or three times -
   each a database round trip - for counts that had just been fetched. */
const MOUNT_FRESH_MS = 10000;
let _loadedAt = 0;

let _counts = { notifications: 0, messages: 0, requests: 0 };
let _profile = null;
let _subs = new Set();
let _timer = null;
let _token = null;

function _emit() {
  _subs.forEach((fn) => fn({ counts: _counts, profile: _profile }));
}

async function _load({ force = false } = {}) {
  if (!_token) return;
  if (!force && Date.now() - _loadedAt < MOUNT_FRESH_MS) return;
  try {
    const c = await fetchUnreadCounts(_token);
    _loadedAt = Date.now();
    _counts = c;
    _emit();
  } catch (_) {
    /* Badges are decoration. A failed poll must never surface an error or
       interrupt the page the user is actually working on. */
  }
}

export function refreshBadges() {
  // Always forced: this is called right after accepting or sending
  // something, where showing a stale number is the whole problem.
  _load({ force: true });
}

/* Cleared on logout, otherwise the next account inherits the previous one's
   counts and avatar for up to one poll interval. */
export function resetBadges() {
  _counts = { notifications: 0, messages: 0, requests: 0 };
  _profile = null;
  _emit();
}

export function useNetworkBadges() {
  const { token } = useAuth();
  const [state, setState] = useState({ counts: _counts, profile: _profile });

  useEffect(() => {
    if (!token) {
      _token = null;
      resetBadges();
      return undefined;
    }
    if (_token !== token) {
      // New session: drop the old numbers before the first poll lands.
      _token = token;
      resetBadges();
      _profile = null;
      _loadedAt = 0;
    }

    _subs.add(setState);
    _load();

    // The profile is fetched once, not polled - a name and an avatar do not
    // change on their own, and Profile calls refreshProfile() when they do.
    if (!_profile) {
      fetchMyProfile(token)
        .then((p) => {
          _profile = p;
          _emit();
        })
        .catch(() => {});
    }

    // One interval no matter how many components are mounted.
    if (!_timer) _timer = setInterval(_load, POLL_MS);

    return () => {
      _subs.delete(setState);
      if (_subs.size === 0 && _timer) {
        clearInterval(_timer);
        _timer = null;
      }
    };
  }, [token]);

  return state;
}

/* Called after the profile or avatar is edited. */
export function setCachedProfile(profile) {
  _profile = profile;
  _emit();
}

/* ---------------------------------------------------------------------------
   Topbar
   --------------------------------------------------------------------------- */

export function AppTopbar({ sidebarOpen, onToggleSidebar }) {
  const { user, logout, isAdvocate } = useAuth();
  const navigate = useNavigate();
  const { counts, profile } = useNetworkBadges();

  const onLogout = useCallback(() => {
    resetBadges();
    logout();
    navigate('/');
  }, [logout, navigate]);

  return (
    <header className="ask-topbar">
      <div className="ask-topbar-left">
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggleSidebar}
          aria-label={sidebarOpen ? 'Close menu' : 'Open menu'}
          aria-expanded={sidebarOpen}
        >
          {sidebarOpen ? NavIcon.close : NavIcon.menu}
        </button>
        <Link to="/" className="logo">
          <span className="mark">न्या</span> Nyaya Sathi
        </Link>
      </div>
      <div className="ask-topbar-right">
        <button
          type="button"
          className="nx-bell"
          // Opening the list is what marks them read and clears this
          // dot - Network.jsx does it on view. Clicking the bell used to
          // navigate and leave the dot sitting there.
          onClick={() => navigate('/network?tab=notifications')}
          aria-label={
            counts.notifications
              ? `${counts.notifications} unread notifications`
              : 'Notifications'
          }
        >
          {NavIcon.bell}
          {counts.notifications > 0 && (
            <span className="nx-bell-dot">
              {counts.notifications > 9 ? '9+' : counts.notifications}
            </span>
          )}
        </button>
        <ThemeToggle />
        <Link to="/profile" className="user-chip nx-user-chip">
          {profile?.avatar_url ? (
            <img className="nx-avatar" style={{ width: 26, height: 26 }}
                 src={profile.avatar_url} alt="" />
          ) : (
            <span className="avatar">{user?.name?.[0]?.toUpperCase() || 'U'}</span>
          )}
          {user?.name?.split(' ')[0]}
          <span className={`role-badge ${isAdvocate ? 'advocate' : ''}`}>
            {isAdvocate ? 'Advocate' : 'Member'}
          </span>
        </Link>
        <button type="button" className="btn btn-ghost" onClick={onLogout}>
          Log out
        </button>
      </div>
    </header>
  );
}

/* ---------------------------------------------------------------------------
   Nav rail
   ---------------------------------------------------------------------------
   `active` is the nav id currently showing. `onWorkspace` lets Ask switch
   mode in place instead of navigating and losing its thread. */

/* Which nav group the second half of the rail belongs to. Collapsed by
   default: the rail carries up to nine items for an advocate, and the four
   network ones pushed the conversation history far enough down that it was
   below the fold on a laptop. */
const NAV_GROUP_KEY = 'ns_nav_group';
const GROUP_IDS = ['advocates', 'network', 'messages', 'profile'];

export function AppNav({ active, onWorkspace }) {
  const { isAdvocate, token } = useAuth();
  const navigate = useNavigate();
  const { counts } = useNetworkBadges();

  /* Lazy initialiser, not an effect: the group must already be open on the
     first paint when the page being rendered lives inside it, or /messages
     would load with its own nav entry hidden.

     Reading `active` here rather than deriving `open` from it on every
     render is what keeps the toggle working. If open were
     `stored || activeInGroup`, then on /messages the button could never
     collapse - it would look broken. This way the stored preference is the
     only thing the button fights with, and landing on a group page just
     starts it open. */
  const [groupOpen, setGroupOpen] = useState(() => {
    if (GROUP_IDS.includes(active)) return true;
    try {
      return localStorage.getItem(NAV_GROUP_KEY) === 'open';
    } catch (_) {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(NAV_GROUP_KEY, groupOpen ? 'open' : 'closed');
    } catch (_) {
      /* private mode - the group just reverts to collapsed next time */
    }
  }, [groupOpen]);

  /* Start the page's reads on hover or keyboard focus, before the click.
     Both api.js and networkApi.js de-duplicate, so the page's own effects
     find the request already in flight (or already answered) rather than
     issuing a second one. */
  const warm = (route) => {
    if (!route) return;
    prefetch(route, token);
    prefetchRoute(route, token, { isAdvocate });
  };

  const workspaceItems = [
    { id: 'ask', label: 'Ask', icon: NavIcon.ask },
    // Draft and Review are open to every account, same as /draft and
    // /review on the backend. Only Arguments and Matters are gated:
    // Arguments builds one-sided advocacy, Matters is a case workspace.
    { id: 'draft', label: 'Draft', icon: NavIcon.draft },
    { id: 'review', label: 'Review', icon: NavIcon.review },
    { id: 'argue', label: 'Arguments', icon: NavIcon.argue, advocateOnly: true },
    { id: 'matters', label: 'Matters', icon: NavIcon.matters, advocateOnly: true, route: '/matters' },
  ].filter((i) => !i.advocateOnly || isAdvocate);

  const groupItems = [
    // Advocates do not see this. They have no reason to search the
    // directory, and the backend only accepts a request whose receiver is
    // an advocate - so the only thing an advocate could do here is connect
    // to a peer, which is not what this feature is for.
    {
      id: 'advocates',
      label: 'Find an Advocate',
      icon: NavIcon.find,
      route: '/advocates',
      clientOnly: true,
    },
    {
      id: 'network',
      // Same page either way; only the framing differs by who is reading it.
      label: isAdvocate ? 'Clients & Requests' : 'My Advocates',
      icon: NavIcon.network,
      route: '/network',
      badge: counts.requests,
    },
    {
      id: 'messages',
      label: 'Messages',
      icon: NavIcon.messages,
      route: '/messages',
      badge: counts.messages,
    },
    { id: 'profile', label: 'My Profile', icon: NavIcon.profile, route: '/profile' },
  ].filter((i) => !i.clientOnly || !isAdvocate);

  // Not 'Clients' - that is a tab label on the page this group links to,
  // and the same word in two places made the tab look inert.
  const groupLabel = isAdvocate ? 'Your network' : 'Get help';

  /* Collapsing must not hide an unread count. Summed rather than shown per
     item, because the point at this size is only "open this to see". */
  const groupCount = groupItems.reduce((n, i) => n + (i.badge || 0), 0);

  const renderItem = (m) => (
    <button
      key={m.id}
      type="button"
      role="tab"
      aria-selected={active === m.id}
      className={`ask-nav-item ${active === m.id ? 'active' : ''}`}
      onPointerEnter={() => warm(m.route || (m.id === 'matters' ? '/matters' : '/ask'))}
      onFocus={() => warm(m.route || (m.id === 'matters' ? '/matters' : '/ask'))}
      onClick={() => {
        if (m.route) return navigate(m.route);
        if (onWorkspace) return onWorkspace(m.id);
        navigate(`/ask?mode=${m.id}`);
      }}
    >
      <span className="ask-nav-icon">{m.icon}</span>
      {m.label}
      {m.badge > 0 && (
        <span className="nx-nav-badge">{m.badge > 9 ? '9+' : m.badge}</span>
      )}
    </button>
  );

  return (
    <nav className="ask-nav" role="tablist">
      {workspaceItems.map(renderItem)}

      <div className="ask-sidebar-divider nx-nav-divider" />

      <button
        type="button"
        className="nx-nav-group-toggle"
        aria-expanded={groupOpen}
        onClick={() => setGroupOpen((o) => !o)}
      >
        <span className="nx-nav-group-chevron" aria-hidden="true">
          {NavIcon.chevron}
        </span>
        {groupLabel}
        {!groupOpen && groupCount > 0 && (
          <span className="nx-nav-group-count">
            {groupCount > 9 ? '9+' : groupCount}
          </span>
        )}
      </button>

      {groupOpen && groupItems.map(renderItem)}
    </nav>
  );
}

export function AppSidebarUser() {
  const { user, isAdvocate } = useAuth();
  const { profile } = useNetworkBadges();
  if (!user) return null;
  return (
    <Link to="/profile" className="ask-sidebar-user nx-sidebar-user">
      {profile?.avatar_url ? (
        <img className="nx-avatar" style={{ width: 32, height: 32 }}
             src={profile.avatar_url} alt="" />
      ) : (
        <span className="avatar">{user.name?.[0]?.toUpperCase() || 'U'}</span>
      )}
      <div className="ask-sidebar-user-text">
        <div className="name">{user.name}</div>
        <div className="sub">
          {isAdvocate ? 'Advocate' : 'Member'}
          {profile?.city || user.state
            ? ` \u00b7 ${profile?.city || user.state}`
            : ''}
        </div>
      </div>
    </Link>
  );
}

/* ---------------------------------------------------------------------------
   Page shell
   ---------------------------------------------------------------------------
   Every network page is the same frame: topbar, collapsible rail, scrolling
   body. Wrapping it once means a change to the frame doesn't have to be
   repeated across five pages. */

/* `fill` keeps the shell bounded to the viewport at every width, for pages
   whose content is a fixed column with its own scroll region (an open
   conversation). Without it, below 900px the shell falls back to ordinary
   page scroll - which suits list and profile pages. See .ask-app-fill. */
export function NetworkPage({ active, fill = false, children }) {
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem('ns_sidebar') !== 'closed'
  );

  useEffect(() => {
    localStorage.setItem('ns_sidebar', sidebarOpen ? 'open' : 'closed');
  }, [sidebarOpen]);

  return (
    <div className={`ask-app ${fill ? 'ask-app-fill' : ''}`}>
      <AppTopbar
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((o) => !o)}
      />
      <main className="ask-shell">
        <div className="ask-layout">
          <aside className={`ask-sidebar ${sidebarOpen ? '' : 'collapsed'}`}>
            <AppNav active={active} />
            <div className="nx-sidebar-spacer" />
            <AppSidebarUser />
          </aside>
          <div className="ask-main">{children}</div>
        </div>
      </main>
    </div>
  );
}