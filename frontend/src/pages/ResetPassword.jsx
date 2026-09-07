import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { resetPassword } from '../api';

/* Supabase appends the recovery token to the URL as a fragment, not a query
   string: /reset#access_token=...&type=recovery. A fragment never reaches a
   server, which is the point - it stays in the browser until we hand it to
   our own backend deliberately. */
function tokenFromHash() {
  const hash = window.location.hash.replace(/^#/, '');
  if (!hash) return { token: '', error: '' };
  const params = new URLSearchParams(hash);
  return {
    token: params.get('access_token') || '',
    error: params.get('error_description') || params.get('error') || '',
  };
}

export default function ResetPassword() {
  const navigate = useNavigate();
  const [token, setToken] = useState('');
  const [linkError, setLinkError] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const { token: t, error: e } = tokenFromHash();
    setToken(t);
    if (e) setLinkError(e.replace(/\+/g, ' '));
    /* Clear the token out of the address bar so it isn't left in history or
       copied along if the user shares the URL. */
    if (t) window.history.replaceState(null, '', window.location.pathname);
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (password !== confirm) {
      setError("Those two passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await resetPassword({ access_token: token, new_password: password });
      setDone(true);
      setTimeout(() => navigate('/login'), 2500);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="auth-shell">
        <div className="auth-card">
          <h1>Password updated</h1>
          <p className="auth-sub">
            You can log in with your new password now. Taking you there&hellip;
          </p>
          <div className="auth-switch">
            <Link to="/login">Go to log in</Link>
          </div>
        </div>
      </div>
    );
  }

  if (!token) {
    return (
      <div className="auth-shell">
        <div className="auth-card">
          <h1>That link didn&apos;t work</h1>
          <p className="auth-sub">
            {linkError ||
              'This reset link is missing its token, or it has already been used. Reset links expire after about an hour.'}
          </p>
          <div className="auth-switch">
            <Link to="/forgot">Request a new link</Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <h1>Set a new password</h1>
        <p className="auth-sub">Pick something you haven&apos;t used here before.</p>

        {error && <div className="form-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="password">New password</label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
            />
          </div>
          <div className="field">
            <label htmlFor="confirm">Confirm new password</label>
            <input
              id="confirm"
              type="password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="Type it again"
            />
          </div>
          <button className="btn btn-primary btn-block" type="submit" disabled={submitting}>
            {submitting ? <span className="spinner" /> : 'Update password'}
          </button>
        </form>
      </div>
    </div>
  );
}