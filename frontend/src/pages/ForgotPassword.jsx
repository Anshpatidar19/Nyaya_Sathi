import { useState } from 'react';
import { Link } from 'react-router-dom';
import { forgotPassword } from '../api';

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  /* The backend never says whether an address is registered, so neither does
     this screen - otherwise anyone could use it to enumerate accounts. */
  if (sent) {
    return (
      <div className="auth-shell">
        <div className="auth-card">
          <h1>Check your inbox</h1>
          <p className="auth-sub">
            If <strong>{email}</strong> has an account, a reset link is on its way.
            It expires in about an hour.
          </p>
          <p className="auth-sub">
            Nothing arrived? Look in spam, or{' '}
            <button type="button" className="link-btn" onClick={() => setSent(false)}>
              try a different address
            </button>
            .
          </p>
          <div className="auth-switch">
            <Link to="/login">Back to log in</Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <h1>Reset your password</h1>
        <p className="auth-sub">
          Enter the email you signed up with and we&apos;ll send you a link to set a new password.
        </p>

        {error && <div className="form-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
          <button className="btn btn-primary btn-block" type="submit" disabled={submitting}>
            {submitting ? <span className="spinner" /> : 'Send reset link'}
          </button>
        </form>

        <div className="auth-switch">
          Remembered it? <Link to="/login">Log in</Link>
        </div>
      </div>
    </div>
  );
}