import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { resendConfirmation } from '../api';

const STATES = [
  'Madhya Pradesh', 'Maharashtra', 'Delhi', 'Karnataka', 'Tamil Nadu',
  'Uttar Pradesh', 'West Bengal', 'Gujarat', 'Rajasthan', 'Telangana', 'Other',
];

// Two account types. The choice decides which tools appear once you're in -
// Draft and Review are advocate-only. Nothing here is verified.
const ROLES = [
  {
    id: 'user',
    label: 'User',
    hint: 'Understand BNS, the Constitution, and Indian law in plain language',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
           strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="8" r="3.5" />
        <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
      </svg>
    ),
  },
  {
    id: 'advocate',
    label: 'Advocate',
    hint: 'Research case law, draft documents, and red-line contracts',
    // Scales of justice - the recognizable legal symbol, rather than the
    // previous courthouse-pillars icon which read ambiguously at this size.
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
           strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3v15" />
        <path d="M8 21h8" />
        <path d="M5 7h5M14 7h5" />
        <path d="M5 7l-3 6a3 3 0 0 0 6 0z" />
        <path d="M19 7l-3 6a3 3 0 0 0 6 0z" />
      </svg>
    ),
  },
];

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({
    name: '',
    email: '',
    password: '',
    state: 'Madhya Pradesh',
    role: 'user',
  });
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [sentTo, setSentTo] = useState('');
  const [resent, setResent] = useState(false);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  const selected = ROLES.find((r) => r.id === form.role);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (form.password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    setSubmitting(true);
    try {
      const data = await register(form);
      if (data.confirmation_required) {
        setSentTo(data.email || form.email);
      } else {
        navigate('/ask');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (sentTo) {
    return (
      <div className="auth-shell">
        <div className="auth-card">
          <h1>Check your inbox</h1>
          <p className="auth-sub">
            We've sent a confirmation link to <strong>{sentTo}</strong>.
            Click it, then come back and log in.
          </p>
          <p className="auth-sub" style={{ marginTop: 16 }}>
            Nothing arrived? Look in spam, or{' '}
            <button
              type="button"
              className="link-btn"
              onClick={() => resendConfirmation(sentTo).then(() => setResent(true)).catch(() => setResent(true))}
            >
              send it again
            </button>
            {resent && <span className="auth-sub"> — sent.</span>}
          </p>
          <p className="auth-alt"><Link to="/login">Go to log in</Link></p>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <h1>Create your account</h1>
        <p className="auth-sub">Free to ask. Your questions stay private to your account.</p>

        {error && <div className="form-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="role-picker" role="radiogroup" aria-label="Account type">
            {ROLES.map((r) => (
              <button
                key={r.id}
                type="button"
                role="radio"
                aria-checked={form.role === r.id}
                className={`role-option ${form.role === r.id ? 'active' : ''}`}
                onClick={() => update('role', r.id)}
              >
                <span className="role-icon">{r.icon}</span>
                <span className="role-label">{r.label}</span>
              </button>
            ))}
          </div>
          <p className="role-hint">{selected?.hint}</p>

          <div className="field">
            <label htmlFor="name">Full name</label>
            <input id="name" required value={form.name} onChange={(e) => update('name', e.target.value)} placeholder="Ansh Sharma" />
          </div>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" required value={form.email} onChange={(e) => update('email', e.target.value)} placeholder="you@example.com" />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" type="password" required value={form.password} onChange={(e) => update('password', e.target.value)} placeholder="At least 8 characters" />
          </div>
          <div className="field">
            <label htmlFor="state">Your state</label>
            <select id="state" value={form.state} onChange={(e) => update('state', e.target.value)}>
              {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <button className="btn btn-primary btn-block" type="submit" disabled={submitting}>
            {submitting
              ? <span className="spinner" />
              : form.role === 'advocate' ? 'Create advocate account' : 'Create account'}
          </button>
        </form>

        <div className="auth-switch">
          Already have an account? <Link to="/login">Log in</Link>
        </div>
      </div>
    </div>
  );
}