/* Small pieces shared by the directory, profile, connections and chat.

   They live together because consistency is the whole point: the same
   person must look the same in a search card, a request card, a
   connections row and a chat header. Five copies of "img if there is one,
   initials if not" is how those drift apart. */

import { useEffect, useRef, useState } from 'react';

/* ---------------- avatar ---------------- */

/* Falls back to initials rather than a generic silhouette - a name is more
   useful than a grey outline, and most demo accounts have no picture. */
export function Avatar({ user, size = 40, className = '' }) {
  const [broken, setBroken] = useState(false);
  const name = user?.name || '';
  const initials =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p[0]?.toUpperCase())
      .join('') || 'U';

  const style = { width: size, height: size, fontSize: Math.round(size * 0.36) };

  // `broken` covers the case that matters in practice: an avatar_path whose
  // object was deleted from the bucket. Without it the row shows a broken
  // image icon instead of initials.
  if (user?.avatar_url && !broken) {
    return (
      <img
        className={`nx-avatar ${className}`}
        style={style}
        src={user.avatar_url}
        alt={name}
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <span className={`nx-avatar nx-avatar-initials ${className}`} style={style}>
      {initials}
    </span>
  );
}

/* ---------------- badges ---------------- */

export function DemoBadge({ user }) {
  if (!user?.is_demo) return null;
  return <span className="nx-badge nx-badge-demo">Demo</span>;
}

const STATUS_LABEL = {
  pending: 'Request sent',
  accepted: 'Connected',
  rejected: 'Declined',
  cancelled: 'Cancelled',
};

export function ConnectionBadge({ connection }) {
  const status = connection?.status;
  if (!status || status === 'none') return null;
  // An incoming pending request is not "request sent" from this side.
  const label =
    status === 'pending' && connection.direction === 'incoming'
      ? 'Awaiting your response'
      : STATUS_LABEL[status];
  return <span className={`nx-badge nx-status nx-status-${status}`}>{label}</span>;
}

/* ---------------- empty state ---------------- */

const EMPTY_ICON = {
  inbox: (
    <path d="M3 12.5V7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v5.5M3 12.5h5l1.5 2.5h5l1.5-2.5h5M3 12.5V17a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4.5" />
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 19.5c0-3.2 2.5-5.2 5.5-5.2s5.5 2 5.5 5.2" />
      <path d="M16.5 5.8a3.2 3.2 0 0 1 0 4.4M18 14.6c2.4.5 3.5 2.3 3.5 4.6" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  bell: (
    <>
      <path d="M12 4a5.5 5.5 0 0 0-5.5 5.5c0 3.6-1.2 4.9-1.8 5.5h14.6c-.6-.6-1.8-1.9-1.8-5.5A5.5 5.5 0 0 0 12 4z" />
      <path d="M9.5 17.5a2.5 2.5 0 0 0 5 0" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
};

/* Title + one supporting line + at most one action. An empty state that is
   just a sentence in a dashed box reads as unfinished; this reads as a
   state the product expects to be in. */
export function Empty({ icon, title, text, action, onAction }) {
  return (
    <div className="nx-empty">
      {icon && EMPTY_ICON[icon] && (
        <span className="nx-empty-ic" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            {EMPTY_ICON[icon]}
          </svg>
        </span>
      )}
      {title && <h3 className="nx-empty-title">{title}</h3>}
      {text && <p className="nx-empty-text">{text}</p>}
      {action && onAction && (
        <button
          type="button"
          className="btn btn-ghost sm"
          onClick={() => onAction(action.to)}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

/* ---------------- disclaimer ---------------- */

/* Required by the product boundary: a connection is a channel, not
   representation. Shown on the advocate profile, in the connect dialog and
   at the top of every conversation. */
export const CONNECTION_DISCLAIMER =
  'A connection is an initial communication channel only. It does not create ' +
  'an advocate-client relationship, does not mean the advocate has accepted ' +
  'your case, and messages here are not a formal legal opinion. ' +
  'Representation is subject to the advocate\u2019s agreement.';

export function Disclaimer({ text, compact = false }) {
  return (
    <p className={`nx-disclaimer ${compact ? 'compact' : ''}`}>
      {text || CONNECTION_DISCLAIMER}
    </p>
  );
}

/* ---------------- connect button ---------------- */

/* One component owns every connection state, so the four states can't be
   rendered inconsistently on the card and the profile. */
export function ConnectButton({
  connection,
  busy,
  onConnect,
  onCancel,
  onMessage,
  size = '',
}) {
  const status = connection?.status || 'none';
  const cls = `btn ${size} `;

  if (status === 'accepted') {
    return (
      <button type="button" className={`${cls}btn-primary`} onClick={onMessage}>
        Message
      </button>
    );
  }
  if (status === 'pending' && connection.direction === 'outgoing') {
    return (
      <button
        type="button"
        className={`${cls}btn-ghost`}
        disabled={busy}
        onClick={onCancel}
      >
        {busy ? 'Cancelling\u2026' : 'Cancel request'}
      </button>
    );
  }
  if (status === 'pending' && connection.direction === 'incoming') {
    // They asked you. Answering happens on the requests page, not here.
    return <span className="nx-inline-note">Respond in Requests</span>;
  }
  // none, rejected or cancelled - all offer Connect again.
  return (
    <button
      type="button"
      className={`${cls}btn-primary`}
      disabled={busy}
      onClick={onConnect}
    >
      {busy ? 'Sending\u2026' : 'Connect'}
    </button>
  );
}

/* ---------------- connect dialog ---------------- */

export function ConnectDialog({ advocate, busy, error, onClose, onSend }) {
  const [intro, setIntro] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    ref.current?.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="arg-setup-backdrop" onClick={onClose}>
      <div className="arg-setup" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="arg-setup-x" onClick={onClose}>
          &times;
        </button>
        <h2>Connect with {advocate?.name}</h2>
        <p className="arg-setup-sub">
          Add a short line about what you need help with. Keep case details for
          the conversation itself &mdash; this note is only so the advocate can
          decide whether to accept.
        </p>

        <div className="field">
          <label htmlFor="nx-intro">Your message (optional)</label>
          <textarea
            id="nx-intro"
            ref={ref}
            className="nx-textarea"
            rows={4}
            maxLength={500}
            value={intro}
            onChange={(e) => setIntro(e.target.value)}
            placeholder="e.g. I need help regarding a property dispute with a relative in Indore."
          />
          <div className="nx-counter">{intro.length}/500</div>
        </div>

        {error && <div className="form-error">{error}</div>}

        <Disclaimer compact />

        <div className="nx-dialog-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => onSend(intro.trim())}
          >
            {busy ? 'Sending\u2026' : 'Send request'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------------- confirm dialog ---------------- */

/* Used for declining a request and cancelling one - both are destructive
   and both are one click away from a list. */
export function ConfirmDialog({
  title,
  body,
  confirmLabel = 'Confirm',
  danger = false,
  busy,
  onClose,
  onConfirm,
}) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="arg-setup-backdrop" onClick={onClose}>
      <div
        className="arg-setup nx-confirm"
        onClick={(e) => e.stopPropagation()}
      >
        <h2>{title}</h2>
        {body && <p className="arg-setup-sub">{body}</p>}
        <div className="nx-dialog-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Keep it
          </button>
          <button
            type="button"
            className={`btn ${danger ? 'btn-ghost danger' : 'btn-primary'}`}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? 'Working\u2026' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------------- misc ---------------- */

export function Skeleton({ rows = 3 }) {
  return (
    <div className="nx-list">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="matter-skel" key={i}>
          <span className="skel-ic" />
          <span className="skel-body">
            <span className="skel-line w60" />
            <span className="skel-line w40" />
            <span className="skel-line w80" />
          </span>
        </div>
      ))}
    </div>
  );
}

/* Comma-separated columns come back as one string from the API. Rendering
   them as chips is what makes a card scannable. */
export function Chips({ value, max = 4 }) {
  const parts = (value || '')
    .split(',')
    .map((p) => p.trim())
    .filter(Boolean);
  if (!parts.length) return null;
  const shown = parts.slice(0, max);
  const rest = parts.length - shown.length;
  return (
    <div className="nx-chips">
      {shown.map((p) => (
        <span className="nx-chip" key={p}>
          {p}
        </span>
      ))}
      {rest > 0 && <span className="nx-chip nx-chip-more">+{rest}</span>}
    </div>
  );
}

/* Timestamps from the API are naive UTC (no trailing Z), which the browser
   would otherwise read as local time and show as hours out. */
export function parseUtc(value) {
  if (!value) return null;
  const s = typeof value === 'string' && !/[Zz]|[+-]\d\d:?\d\d$/.test(value)
    ? `${value}Z`
    : value;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function timeAgo(value) {
  const d = parseUtc(value);
  if (!d) return '';
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

export function clockTime(value) {
  const d = parseUtc(value);
  if (!d) return '';
  return d.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' });
}

export function dayLabel(value) {
  const d = parseUtc(value);
  if (!d) return '';
  const today = new Date();
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, today)) return 'Today';
  const yest = new Date(today);
  yest.setDate(today.getDate() - 1);
  if (same(d, yest)) return 'Yesterday';
  return d.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric',
  });
}

export function locationOf(user, fallbackCity) {
  const city = fallbackCity || user?.city;
  return [city, user?.state].filter(Boolean).join(', ');
}