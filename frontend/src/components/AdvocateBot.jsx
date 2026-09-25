/* The Find an Advocate assistant.

   Self-contained: this renders its own launcher and its own panel, so the
   Advocates page mounts it with one line and its layout is untouched. The
   panel is fixed to the right edge rather than pushing the results grid,
   because the grid is the thing people came for - the assistant is a way
   into it, not a replacement for it.

   Two rules this component exists to keep:

   1. Nothing here invents a ranking or a reason. The order and every
      "why" line arrive from the server, computed in advocate_match.py
      from real profile fields. This file only draws them.

   2. No number is shown. Advocates are ordered nearest first - the
      user's city, then their state, then elsewhere - and by how well the
      profile fits the matter within each of those. A visible score read
      as a rating, and the directory has no ratings data; there are no
      stars, and no advocate is called "best".

   Connect and View profile reuse the page's existing handlers, so a
   connection started from the assistant is the same connection, with the
   same dialog, as one started from a card in the grid. */

import { useEffect, useRef, useState } from 'react';
import { ADVOCATE_BOT_KEY } from '../AuthContext';
import { recommendAdvocatesStream } from '../advocateBotApi';
import JusticeMark from './JusticeMark';
import { Avatar, Chips, DemoBadge, locationOf } from './NetworkBits';
import '../advocatebot.css';

const OPENERS = [
  'I need a criminal lawyer in Indore',
  'Find an advocate for a property dispute',
  'Someone experienced in cheque bounce cases',
  'A family law advocate who speaks Marathi',
];

/* Why the thread is persisted at all.

   "View profile" is a real navigation: it unmounts this page, and with it
   every piece of React state the assistant holds. Coming back used to
   land on an empty panel, which meant the one action the feature exists
   to encourage - go and look at a recommended advocate - was also the
   action that destroyed the results. Comparing two advocates was
   impossible.

   sessionStorage rather than localStorage, following the precedent in
   AuthContext: what someone types here describes their legal problem, so
   it should die with the tab, and AuthContext clears the key on both sign
   in and sign out so it never leaks across accounts on a shared browser.

   Only the turns and the open/closed state are kept. The draft is not -
   a half-typed sentence reappearing days later is noise, not memory. */
const STORE_LIMIT = 20;

function loadSaved() {
  try {
    const raw = sessionStorage.getItem(ADVOCATE_BOT_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    return Array.isArray(saved?.turns) ? saved : null;
  } catch (_) {
    // Corrupt or unreadable - start fresh rather than crash the page.
    return null;
  }
}

const GREETING =
  'Tell me what you need help with and I will find advocates whose profiles ' +
  'match it. The more specific the matter, the better the match.';

export default function AdvocateBot({ token, onViewProfile, onConnect }) {
  // Read once, on mount, so a return trip from a profile restores exactly
  // what was on screen.
  const [saved] = useState(loadSaved);
  const [open, setOpen] = useState(() => !!saved?.open);
  const [turns, setTurns] = useState(() => saved?.turns || []);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  // Live progress for the turn being answered: one entry per step the
  // server has actually started, in order. The last one is in progress.
  const [steps, setSteps] = useState([]);
  const [error, setError] = useState('');
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [turns, busy, open]);

  // Persist on every change. Trimmed from the front: the newest exchanges
  // are the ones worth coming back to, and an unbounded thread of advocate
  // cards would eventually hit the storage quota.
  useEffect(() => {
    try {
      if (!turns.length && !open) {
        sessionStorage.removeItem(ADVOCATE_BOT_KEY);
        return;
      }
      sessionStorage.setItem(
        ADVOCATE_BOT_KEY,
        JSON.stringify({ open, turns: turns.slice(-STORE_LIMIT) })
      );
    } catch (_) {
      /* quota or private mode - the panel still works, it just forgets */
    }
  }, [open, turns]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  async function send(text) {
    const question = (text ?? draft).trim();
    if (!question || busy) return;

    // Only the text turns go back as history. The advocate cards are this
    // client's rendering of a result, not something the parser should try
    // to read on the next turn.
    const history = turns
      .filter((t) => t.content)
      .map((t) => ({ role: t.role, content: t.content }));

    setTurns((prev) => [...prev, { role: 'user', content: question }]);
    setDraft('');
    setBusy(true);
    setSteps([]);
    setError('');

    try {
      const data = await recommendAdvocatesStream(
        token,
        { message: question, history },
        {
          onStatus: ({ stage, detail }) =>
            setSteps((prev) => [...prev, { stage, detail }]),
        },
      );
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: data.reply,
          kind: data.kind,
          items: data.items || [],
          understood: data.understood || {},
          widened: data.widened,
        },
      ]);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      setSteps([]);
    }
  }

  if (!open) {
    // Just the mark. A floating button carrying two lines of copy competes
    // with the page it floats over; the icon alone is the convention for
    // this control, and the label lives in the tooltip and the accessible
    // name where a screen reader will still find it.
    return (
      <button
        type="button"
        className="ab-fab"
        onClick={() => {
          setOpen(true);
          setTimeout(() => inputRef.current?.focus(), 80);
        }}
        title="Find an Advocate - ask the assistant"
        aria-label="Open the Find an Advocate assistant"
      >
        <JusticeMark variant="bust" size={64} decorative />
        {!!turns.length && <span className="ab-fab-dot" aria-hidden="true" />}
      </button>
    );
  }

  return (
    <aside className="ab-panel" aria-label="Find an Advocate assistant">
      <header className="ab-head">
        {/* She is the mark for this surface, so she sits at full detail in
            the header rather than being shrunk into a chat avatar. */}
        <JusticeMark height={54} decorative className="ab-mark" />
        <div className="ab-head-text">
          <strong>Find an Advocate</strong>
          <span>Nearest advocates first, matched on their profile</span>
        </div>
        {!!turns.length && (
          <button
            type="button"
            className="ab-clear"
            onClick={() => setTurns([])}
            title="Clear this conversation"
          >
            Clear
          </button>
        )}
        <button
          type="button"
          className="ab-x"
          onClick={() => setOpen(false)}
          aria-label="Close assistant"
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
               strokeLinecap="round" aria-hidden="true">
            <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" />
          </svg>
        </button>
      </header>

      <div className="ab-thread">
        {!turns.length && (
          <div className="ab-intro">
            <p>{GREETING}</p>
            <div className="ab-openers">
              {OPENERS.map((o) => (
                <button type="button" key={o} className="ab-opener" onClick={() => send(o)}>
                  {o}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, i) =>
          turn.role === 'user' ? (
            <div className="ab-you" key={i}>
              {turn.content}
            </div>
          ) : (
            <div className="ab-reply" key={i}>
              <p className="ab-reply-text">{turn.content}</p>

              {turn.kind === 'matches' && (
                <Understood understood={turn.understood} />
              )}

              {(turn.items || []).map((item) => (
                <AdvocateResult
                  key={item.card.user.id}
                  item={item}
                  onViewProfile={onViewProfile}
                  onConnect={onConnect}
                />
              ))}

              {turn.kind === 'matches' && !!turn.items?.length && (
                <p className="ab-fineprint">
                  Advocates in your city come first, then elsewhere in your
                  state, then the rest. Within each, they are ordered by how
                  closely their profile fits your matter - practice area,
                  relevant experience, years in practice and language. This is
                  not a rating: no reviews or case outcomes are used.
                </p>
              )}
            </div>
          )
        )}

        {busy && (
          // The server's own steps, as they happen: finished ones ticked,
          // the current one spinning. Nothing here is on a timer.
          <ol className="ab-steps" aria-live="polite">
            {(steps.length ? steps : [{ stage: 'start', detail: 'Starting' }]).map((st, i, all) => {
              const current = i === all.length - 1;
              return (
                <li key={`${st.stage}-${i}`} className={`ab-step${current ? ' is-current' : ' is-done'}`}>
                  {current ? (
                    <span className="spinner spinner-dark" />
                  ) : (
                    <span className="ab-step-tick" aria-hidden="true">✓</span>
                  )}
                  {st.detail}
                </li>
              );
            })}
          </ol>
        )}
        {error && <div className="ab-error">{error}</div>}
        <div ref={endRef} />
      </div>

      <div className="ab-dock">
        <textarea
          ref={inputRef}
          rows={1}
          className="ab-input"
          value={draft}
          placeholder={'Describe your legal matter\u2026'}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          aria-label="Describe your legal matter"
        />
        <button
          type="button"
          className="ab-send"
          onClick={() => send()}
          disabled={busy || !draft.trim()}
          aria-label="Send"
        >
          {busy ? (
            <span className="spinner" />
          ) : (
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 10l14-6-5.2 14L9.4 12z" />
            </svg>
          )}
        </button>
      </div>
    </aside>
  );
}

/* What the parser took from the sentence. Shown because a wrong reading is
   the most likely way this feature fails, and a visible "Indore · cheque
   bounce" lets someone correct it in one message instead of wondering why
   the results look odd. */
function Understood({ understood }) {
  const place = understood.city || understood.state;
  const bits = [
    understood.practice_area,
    ...(understood.matter_keywords || []),
    place
      ? understood.location_source === 'profile'
        ? `${place} (your location)`
        : place
      : null,
    understood.language,
    understood.min_experience ? `${understood.min_experience}+ years` : null,
  ].filter(Boolean);

  if (!bits.length) return null;
  return (
    <div className="ab-understood">
      <span className="ab-understood-label">Searched for</span>
      {bits.map((b, i) => (
        <span className="ab-token" key={i}>
          {b}
        </span>
      ))}
    </div>
  );
}

function AdvocateResult({ item, onViewProfile, onConnect }) {
  const { card, reasons = [], location_match: near } = item;
  const connected = card.connection?.status === 'accepted';

  return (
    <article className="ab-card">
      <div className="ab-card-top">
        <Avatar user={card.user} size={44} />
        <div className="ab-ident">
          <div className="ab-name-row">
            <h4>{card.user.name}</h4>
            <DemoBadge user={card.user} />
          </div>
          <div className="ab-sub">{card.specialization || 'Advocate'}</div>
          <div className="ab-meta">
            {locationOf(card.user, card.practice_city)}
            {card.years_experience != null && (
              <>
                <span className="ab-dot">&middot;</span>
                {card.years_experience} yrs
              </>
            )}
          </div>
        </div>
        {near === 'city' && <span className="ab-near">Near you</span>}
      </div>

      {!!reasons.length && (
        <ul className="ab-reasons">
          {reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}

      <Chips value={card.practice_areas} max={3} />

      <div className="ab-actions">
        <button
          type="button"
          className="btn btn-ghost sm"
          onClick={() => onViewProfile?.(card.user.id)}
        >
          View profile
        </button>
        <button
          type="button"
          className="btn btn-primary sm"
          onClick={() => onConnect?.(card.user, card.connection)}
        >
          {connected ? 'Message' : 'Connect'}
        </button>
      </div>
    </article>
  );
}