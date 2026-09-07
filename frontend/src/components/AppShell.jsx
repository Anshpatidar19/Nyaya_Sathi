import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import ThemeToggle from './ThemeToggle';

/* The topbar and nav rail, shared so /ask and /matters can't drift apart.
   Ask keeps its own body layout; this only owns the chrome around it. */

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
};

export function AppTopbar({ sidebarOpen, onToggleSidebar }) {
  const { user, logout, isAdvocate } = useAuth();
  const navigate = useNavigate();

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
        <ThemeToggle />
        <span className="user-chip">
          <span className="avatar">{user?.name?.[0]?.toUpperCase() || 'U'}</span>
          {user?.name?.split(' ')[0]}
          <span className={`role-badge ${isAdvocate ? 'advocate' : ''}`}>
            {isAdvocate ? 'Advocate' : 'Member'}
          </span>
        </span>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => { logout(); navigate('/'); }}
        >
          Log out
        </button>
      </div>
    </header>
  );
}

/* `active` is the nav id currently showing. `onWorkspace` lets Ask switch
   mode in place instead of navigating and losing its thread. */
export function AppNav({ active, onWorkspace }) {
  const { isAdvocate } = useAuth();
  const navigate = useNavigate();

  const items = [
    { id: 'ask', label: 'Ask', icon: NavIcon.ask },
    { id: 'draft', label: 'Draft', icon: NavIcon.draft, advocateOnly: true },
    { id: 'review', label: 'Review', icon: NavIcon.review, advocateOnly: true },
    { id: 'argue', label: 'Arguments', icon: NavIcon.argue, advocateOnly: true },
    { id: 'matters', label: 'Matters', icon: NavIcon.matters, advocateOnly: true, route: '/matters' },
  ].filter((i) => !i.advocateOnly || isAdvocate);

  return (
    <nav className="ask-nav" role="tablist">
      {items.map((m) => (
        <button
          key={m.id}
          type="button"
          role="tab"
          aria-selected={active === m.id}
          className={`ask-nav-item ${active === m.id ? 'active' : ''}`}
          onClick={() => {
            if (m.route) return navigate(m.route);
            if (onWorkspace) return onWorkspace(m.id);
            navigate(`/ask?mode=${m.id}`);
          }}
        >
          <span className="ask-nav-icon">{m.icon}</span>
          {m.label}
        </button>
      ))}
    </nav>
  );
}

export function AppSidebarUser() {
  const { user, isAdvocate } = useAuth();
  if (!user) return null;
  return (
    <div className="ask-sidebar-user">
      <span className="avatar">{user.name?.[0]?.toUpperCase() || 'U'}</span>
      <div className="ask-sidebar-user-text">
        <div className="name">{user.name}</div>
        <div className="sub">
          {isAdvocate ? 'Advocate' : 'Member'}
          {user.state ? ` · ${user.state}` : ''}
        </div>
      </div>
    </div>
  );
}