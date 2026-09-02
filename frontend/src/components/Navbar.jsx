import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';

export default function Navbar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate('/');
  }

  return (
    <header className="site-header">
      <nav className="navbar">
        <Link to="/" className="logo">
          <span className="mark">न्या</span> Nyaya Sathi
        </Link>
        <div className="navlinks">
          <Link to="/#features">What it does</Link>
          <Link to="/#how">How it works</Link>
          <Link to="/#trust">Trust &amp; safety</Link>
          <Link to="/#faq">FAQ</Link>
        </div>
        <div className="navcta">
          {user ? (
            <>
              <Link to="/ask" className="btn btn-ghost">Ask a question</Link>
              <span className="user-chip">
                <span className="avatar">{user.name?.[0]?.toUpperCase() || 'U'}</span>
                {user.name.split(' ')[0]}
              </span>
              <button className="btn btn-ghost" onClick={handleLogout}>Log out</button>
            </>
          ) : (
            <>
              <Link to="/login" className="btn btn-ghost">Log in</Link>
              <Link to="/register" className="btn btn-primary">Ask a question</Link>
            </>
          )}
        </div>
      </nav>
    </header>
  );
}
