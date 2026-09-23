/* AI document intelligence for the advocate-client chat - and, through
   AnalysisCard's "standalone" variant, for an advocate's own upload on the
   Ask page.

   The whole feature lives here and hangs off three seams in Messages.jsx:
   a menu button next to an attachment, a card under it, and a panel beside
   the conversation. Nothing else about the chat changes, which is the
   point - this is meant to read as part of the workspace, not as a second
   application bolted onto it.

   Where the output goes, and why:

     Analyze          a compact card UNDER the document, in the stream. It
                      is about that message, so it belongs with it. Sending
                      the extraction into the chat as a message would put
                      the advocate's working notes in front of the client
                      and bury the conversation under a wall of AI text.
     Suggest Questions / Ask AI
                      the side panel, because both are things you work
                      from while reading the conversation. The chat stays
                      visible and live - it keeps polling behind the panel.

   State is owned by useDocIntel() and keyed by document id, so two
   documents in the same thread hold their own analysis, question list and
   Q&A thread, and switching between them loses nothing.

   The Q&A thread is deliberately session-only. It is scratch work about a
   file, not a record of the matter - anything worth keeping gets sent to
   the client or written into the Matter. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  analyzeDocument,
  askAboutDocument,
  suggestQuestions,
} from '../docIntelApi';
import '../docintel.css';

/* ---------------------------------------------------------------- icons */

const I = {
  dots: (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <circle cx="10" cy="4.5" r="1.5" />
      <circle cx="10" cy="10" r="1.5" />
      <circle cx="10" cy="15.5" r="1.5" />
    </svg>
  ),
  spark: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 2.5 9.4 6.6 13.5 8 9.4 9.4 8 13.5 6.6 9.4 2.5 8 6.6 6.6z" />
      <path d="M14.5 12.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
    </svg>
  ),
  question: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M7.9 7.7a2.1 2.1 0 1 1 2.9 2c-.6.3-.9.8-.9 1.4v.4" />
      <path d="M10 14.4h.01" />
    </svg>
  ),
  chat: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M17 11.2A3.3 3.3 0 0 1 13.7 14.5H7.4L3 17.5V5.8A3.3 3.3 0 0 1 6.3 2.5h7.4A3.3 3.3 0 0 1 17 5.8z" />
    </svg>
  ),
  warn: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 2.8 18 16.5H2z" />
      <path d="M10 8v3.4" />
      <path d="M10 14.1h.01" />
    </svg>
  ),
  expand: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3h5v5" />
      <path d="M17 3l-6.2 6.2" />
      <path d="M8.5 17H3v-5.5" />
      <path d="M3 17l6.2-6.2" />
    </svg>
  ),
  close: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" aria-hidden="true">
      <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" />
    </svg>
  ),
  check: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4.5 10.5l3.5 3.5 7.5-8" />
    </svg>
  ),
  send: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 10l14-6-5.2 14L9.4 12z" />
    </svg>
  ),
};

/* Everything the backend can read. Photos, scans and handwriting are read
   by Gemini OCR server-side, so images are analysable like any PDF. */
const READABLE = /^(application\/pdf|text\/plain|application\/msword|application\/vnd\.openxmlformats-officedocument\.wordprocessingml\.document|image\/(jpeg|png|webp|heic|heif))$/;

function readableReason(attachment) {
  const type = attachment?.content_type || '';
  if (READABLE.test(type)) return null;
  return 'Only PDF, Word, photo and text files can be analysed.';
}

const TABS = [
  { id: 'analysis', label: 'Analysis' },
  { id: 'questions', label: 'Questions' },
  { id: 'ask', label: 'Ask AI' },
];

const ASK_SUGGESTIONS = [
  'What are the main allegations?',
  'What are the important dates?',
  'What evidence is mentioned?',
  'What information is missing?',
  'Which legal sections are explicitly mentioned?',
  'Summarise this document in 5 points.',
  'What should I clarify with the client?',
];

const EMPTY_DOC = {
  analysis: null,
  analysisLoading: false,
  analysisError: '',
  questions: null,
  questionsLoading: false,
  questionsError: '',
  chat: [],
  asking: false,
  askError: '',
};

/* ------------------------------------------------------------------ hook */

export function useDocIntel({ token, threadId, enabled = false, onInsertText }) {
  // documentId -> { attachment, ...EMPTY_DOC }
  const [docs, setDocs] = useState({});
  // { docId, tab } | null
  const [panel, setPanel] = useState(null);

  // A thread switch must not carry another conversation's analysis across.
  useEffect(() => {
    setDocs({});
    setPanel(null);
  }, [threadId]);

  const patch = useCallback((docId, changes) => {
    setDocs((prev) => ({
      ...prev,
      [docId]: { ...EMPTY_DOC, ...(prev[docId] || {}), ...changes },
    }));
  }, []);

  const loadAnalysis = useCallback(
    async (attachment, { force = false } = {}) => {
      const id = attachment.id;
      patch(id, { attachment, analysisLoading: true, analysisError: '' });
      try {
        const data = await analyzeDocument(token, threadId, id, { force });
        patch(id, { analysis: data, analysisLoading: false });
        return data;
      } catch (err) {
        patch(id, { analysisLoading: false, analysisError: err.message });
        return null;
      }
    },
    [patch, token, threadId]
  );

  const loadQuestions = useCallback(
    async (attachment, { force = false } = {}) => {
      const id = attachment.id;
      patch(id, { attachment, questionsLoading: true, questionsError: '' });
      try {
        const data = await suggestQuestions(token, threadId, id, { force });
        patch(id, { questions: data, questionsLoading: false });
        return data;
      } catch (err) {
        patch(id, { questionsLoading: false, questionsError: err.message });
        return null;
      }
    },
    [patch, token, threadId]
  );

  const sendAsk = useCallback(
    async (attachment, question) => {
      const id = attachment.id;
      const asked = (question || '').trim();
      if (!asked) return;

      // Read the thread off the latest state rather than a closure over it,
      // so two quick questions don't both send the same history.
      let history = [];
      setDocs((prev) => {
        const doc = { ...EMPTY_DOC, ...(prev[id] || {}), attachment };
        history = doc.chat;
        return {
          ...prev,
          [id]: {
            ...doc,
            chat: [...doc.chat, { role: 'user', content: asked }],
            asking: true,
            askError: '',
          },
        };
      });

      try {
        const data = await askAboutDocument(token, threadId, id, {
          question: asked,
          history,
        });
        setDocs((prev) => {
          const doc = { ...EMPTY_DOC, ...(prev[id] || {}) };
          return {
            ...prev,
            [id]: {
              ...doc,
              chat: [...doc.chat, { role: 'assistant', ...data }],
              asking: false,
            },
          };
        });
      } catch (err) {
        patch(id, { asking: false, askError: err.message });
      }
    },
    [patch, token, threadId]
  );

  /* What the three-dot menu does. Analyze stays in the stream; the other
     two open the panel, because they are worked from rather than read
     once. */
  const run = useCallback(
    (attachment, action) => {
      const id = attachment.id;
      const existing = docs[id];

      if (action === 'analyze') {
        if (!existing?.analysis && !existing?.analysisLoading) loadAnalysis(attachment);
        else patch(id, { attachment });
        return;
      }

      setPanel({ docId: id, tab: action === 'ask' ? 'ask' : 'questions' });
      patch(id, { attachment });

      if (action === 'questions' && !existing?.questions && !existing?.questionsLoading) {
        loadQuestions(attachment);
      }
      // The panel's Analysis tab needs the extraction too, and the questions
      // are built on top of it server-side - so warm it either way.
      if (!existing?.analysis && !existing?.analysisLoading) loadAnalysis(attachment);
    },
    [docs, loadAnalysis, loadQuestions, patch]
  );

  const openPanel = useCallback(
    (attachment, tab) => {
      patch(attachment.id, { attachment });
      setPanel({ docId: attachment.id, tab });
      const existing = docs[attachment.id];
      if (tab === 'questions' && !existing?.questions && !existing?.questionsLoading) {
        loadQuestions(attachment);
      }
      if (!existing?.analysis && !existing?.analysisLoading) loadAnalysis(attachment);
    },
    [docs, loadAnalysis, loadQuestions, patch]
  );

  const menuFor = useCallback(
    (attachment) => {
      if (!enabled || !attachment) return null;
      return (
        <DocMenu
          attachment={attachment}
          blocked={readableReason(attachment)}
          onPick={(action) => run(attachment, action)}
        />
      );
    },
    [enabled, run]
  );

  const cardFor = useCallback(
    (attachment) => {
      if (!enabled || !attachment) return null;
      const state = docs[attachment.id];
      // Nothing is shown until the advocate asks for it. An AI card under
      // every file the moment it arrives is noise, and it would also mean
      // a Gemini call per attachment on every thread open.
      if (!state || (!state.analysis && !state.analysisLoading && !state.analysisError)) {
        return null;
      }
      return (
        <AnalysisCard
          state={state}
          onExpand={() => openPanel(attachment, 'analysis')}
          onQuestions={() => openPanel(attachment, 'questions')}
          onAsk={() => openPanel(attachment, 'ask')}
          onRetry={() => loadAnalysis(attachment, { force: true })}
          onDismiss={() => {
            setDocs((prev) => {
              const next = { ...prev };
              delete next[attachment.id];
              return next;
            });
            setPanel((p) => (p?.docId === attachment.id ? null : p));
          }}
        />
      );
    },
    [docs, enabled, loadAnalysis, openPanel]
  );

  const panelNode =
    enabled && panel ? (
      <DocIntelPanel
        state={docs[panel.docId] || EMPTY_DOC}
        attachment={docs[panel.docId]?.attachment}
        tab={panel.tab}
        onTab={(tab) => {
          setPanel((p) => ({ ...p, tab }));
          const attachment = docs[panel.docId]?.attachment;
          const state = docs[panel.docId];
          if (attachment && tab === 'questions' && !state?.questions && !state?.questionsLoading) {
            loadQuestions(attachment);
          }
        }}
        onClose={() => setPanel(null)}
        onReloadAnalysis={() => {
          const att = docs[panel.docId]?.attachment;
          if (att) loadAnalysis(att, { force: true });
        }}
        onReloadQuestions={() => {
          const att = docs[panel.docId]?.attachment;
          if (att) loadQuestions(att, { force: true });
        }}
        onAsk={(q) => {
          const att = docs[panel.docId]?.attachment;
          if (att) sendAsk(att, q);
        }}
        onInsertText={onInsertText}
      />
    ) : null;

  return {
    enabled,
    menuFor,
    cardFor,
    panel: panelNode,
    panelOpen: !!panelNode,
  };
}

/* ------------------------------------------------------------------ menu */

function DocMenu({ attachment, blocked, onPick }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const items = [
    { id: 'analyze', icon: I.spark, label: 'Analyze Document' },
    { id: 'questions', icon: I.question, label: 'Suggest Questions' },
    { id: 'ask', icon: I.chat, label: 'Ask AI About Document' },
  ];

  return (
    <div className="di-menu-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`di-menu-btn ${open ? 'open' : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`AI options for ${attachment.filename}`}
        title="AI options"
      >
        {I.dots}
      </button>

      {open && (
        <div className="di-menu" role="menu">
          {items.map((item) => (
            <button
              type="button"
              key={item.id}
              className="di-menu-item"
              role="menuitem"
              disabled={!!blocked}
              onClick={() => {
                setOpen(false);
                onPick(item.id);
              }}
            >
              <span className="di-menu-ic">{item.icon}</span>
              {item.label}
            </button>
          ))}
          {blocked && <div className="di-menu-note">{blocked}</div>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ small parts */

function Spinner({ label }) {
  return (
    <span className="di-loading">
      <span className="spinner spinner-dark" />
      {label}
    </span>
  );
}

function ErrorNote({ message, onRetry }) {
  return (
    <div className="di-error">
      <span>{message}</span>
      {onRetry && (
        <button type="button" className="di-link" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

/* Collapsible, but native: <details> keeps its own state, so a panel with
   nine sections does not need nine pieces of React state that reset every
   time the parent re-renders on a chat poll. */
function Section({ title, count, children, open = false }) {
  if (count === 0) return null;
  return (
    <details className="di-section" open={open}>
      <summary>
        <span className="di-section-title">{title}</span>
        {typeof count === 'number' && <span className="di-count">{count}</span>}
        <span className="di-chev" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
               strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 8l4 4 4-4" />
          </svg>
        </span>
      </summary>
      <div className="di-section-body">{children}</div>
    </details>
  );
}

function severityLabel(level) {
  return level === 'high' ? 'High' : level === 'low' ? 'Low' : 'Medium';
}

/* ------------------------------------------------------- compact card */

/* One card, two contexts.

     variant="chat"        (default) the advocate-client conversation: a
                           compact summary under the file, plus the three
                           chat actions - Full analysis, Suggest questions,
                           Ask AI - which open the side panel.
     variant="standalone"  the Ask page, for the advocate's own upload:
                           the full analysis inline, and none of the three
                           actions. Suggest questions and Ask AI are built
                           around a client on the other side of a thread,
                           and there is no panel on Ask to expand into.

   The analysis itself is not duplicated - standalone renders the same
   AnalysisView the panel's Analysis tab uses. */
export function AnalysisCard({
  state,
  variant = 'chat',
  filename,
  onExpand,
  onQuestions,
  onAsk,
  onRetry,
  onDismiss,
}) {
  const a = state.analysis;

  if (variant === 'standalone') {
    return (
      <div className="di-card di-card-full">
        <div className="di-card-head">
          <span className="di-badge">
            {I.spark}
            AI analysis
          </span>
          {filename && (
            <span className="di-card-file" title={filename}>
              {filename}
            </span>
          )}
          <span className="di-spacer" />
          {onDismiss && (
            <button
              type="button"
              className="di-icon-btn"
              onClick={onDismiss}
              title="Hide this analysis"
              aria-label="Hide this analysis"
            >
              {I.close}
            </button>
          )}
        </div>
        {/* No onTab: without a panel there is nowhere for "Turn these into
            client questions" to go, so AnalysisView leaves it out. */}
        <AnalysisView state={state} onReload={onRetry} />
      </div>
    );
  }

  return (
    <div className="di-card">
      <div className="di-card-head">
        <span className="di-badge">
          {I.spark}
          AI analysis
        </span>
        {a && (
          <>
            <span className="di-type">{a.document_type}</span>
            {a.confidence !== 'high' && (
              <span className="di-conf">{a.confidence} confidence</span>
            )}
          </>
        )}
        <span className="di-spacer" />
        {a && (
          <button type="button" className="di-icon-btn" onClick={onExpand} title="Expand">
            {I.expand}
          </button>
        )}
        <button
          type="button"
          className="di-icon-btn"
          onClick={onDismiss}
          title="Hide this analysis"
          aria-label="Hide this analysis"
        >
          {I.close}
        </button>
      </div>

      {state.analysisLoading && <Spinner label={'Reading the document\u2026'} />}
      {state.analysisError && !state.analysisLoading && (
        <ErrorNote message={state.analysisError} onRetry={onRetry} />
      )}

      {a && !state.analysisLoading && (
        <>
          <p className="di-card-summary">{a.summary}</p>

          <div className="di-facts">
            {!!a.parties.length && <span>{a.parties.length} parties</span>}
            {!!a.dates.length && <span>{a.dates.length} dates</span>}
            {!!a.laws.length && <span>{a.laws.length} provisions</span>}
            {!!a.amounts.length && <span>{a.amounts.length} amounts</span>}
            {!!a.clauses.length && <span>{a.clauses.length} clauses</span>}
          </div>

          {!!a.gaps.length && (
            <button type="button" className="di-gap-strip" onClick={onQuestions}>
              <span className="di-gap-ic">{I.warn}</span>
              {a.gaps.length === 1
                ? '1 information gap detected'
                : `${a.gaps.length} information gaps detected`}
              <span className="di-gap-cta">Turn into questions</span>
            </button>
          )}

          <div className="di-card-actions">
            <button type="button" className="di-btn" onClick={onExpand}>
              Full analysis
            </button>
            <button type="button" className="di-btn primary" onClick={onQuestions}>
              Suggest questions
            </button>
            <button type="button" className="di-btn" onClick={onAsk}>
              Ask AI
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- panel */

function DocIntelPanel({
  state,
  attachment,
  tab,
  onTab,
  onClose,
  onReloadAnalysis,
  onReloadQuestions,
  onAsk,
  onInsertText,
}) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!attachment) return null;

  return (
    <aside className="di-panel" aria-label="Document intelligence">
      <header className="di-panel-head">
        <div className="di-panel-ident">
          <span className="di-panel-eyebrow">
            {I.spark}
            Document intelligence
          </span>
          <strong title={attachment.filename}>{attachment.filename}</strong>
        </div>
        <button
          type="button"
          className="di-icon-btn"
          onClick={onClose}
          aria-label="Close panel"
        >
          {I.close}
        </button>
      </header>

      <nav className="di-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            type="button"
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`di-tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => onTab(t.id)}
          >
            {t.label}
            {t.id === 'questions' && state.questions?.questions?.length ? (
              <span className="di-count">{state.questions.questions.length}</span>
            ) : null}
          </button>
        ))}
      </nav>

      <div className={`di-panel-body ${tab === 'ask' ? 'flush' : ''}`}>
        {tab === 'analysis' && (
          <AnalysisView state={state} onReload={onReloadAnalysis} onTab={onTab} />
        )}
        {tab === 'questions' && (
          <QuestionsView
            state={state}
            onReload={onReloadQuestions}
            onInsertText={onInsertText}
          />
        )}
        {tab === 'ask' && <AskView state={state} onAsk={onAsk} />}
      </div>
    </aside>
  );
}

/* ---------------------------------------------------------- analysis tab */

function AnalysisView({ state, onReload, onTab }) {
  const a = state.analysis;

  if (state.analysisLoading) return <Spinner label={'Reading the document\u2026'} />;
  if (state.analysisError) {
    return <ErrorNote message={state.analysisError} onRetry={onReload} />;
  }
  if (!a) return null;

  return (
    <div className="di-stack">
      <div className="di-head-row">
        <span className="di-type lg">{a.document_type}</span>
        <span className={`di-conf-dot ${a.confidence}`} title={`${a.confidence} confidence`} />
        <span className="di-spacer" />
        {onReload && (
          <button type="button" className="di-link" onClick={onReload}>
            Re-run
          </button>
        )}
      </div>

      <p className="di-summary">{a.summary}</p>

      {a.truncated && (
        <div className="di-note">
          This document is long - the analysis covers the first part of it.
        </div>
      )}

      {!!a.gaps.length && (
        <div className="di-gaps">
          <div className="di-gaps-head">
            <span className="di-gap-ic">{I.warn}</span>
            Missing or unsupported information
          </div>
          {a.gaps.map((g, i) => (
            <div className="di-gap" key={i}>
              <span className={`di-sev ${g.severity}`}>{severityLabel(g.severity)}</span>
              <div>
                <p className="di-gap-issue">{g.issue}</p>
                {g.why_it_matters && <p className="di-gap-why">{g.why_it_matters}</p>}
              </div>
            </div>
          ))}
          {onTab && (
            <button type="button" className="di-btn primary wide" onClick={() => onTab('questions')}>
              Turn these into client questions
            </button>
          )}
        </div>
      )}

      <Section title="Parties" count={a.parties.length} open>
        <ul className="di-list">
          {a.parties.map((p, i) => (
            <li key={i}>
              <strong>{p.name}</strong>
              <span className="di-role">{p.role}</span>
              {p.organisation && <span className="di-sub">{p.organisation}</span>}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Important dates" count={a.dates.length} open>
        <ul className="di-timeline">
          {a.dates.map((d, i) => (
            <li key={i}>
              <span className="di-date">{d.date}</span>
              <span>{d.event}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Claims and allegations" count={a.claims.length}>
        <ul className="di-bullets">
          {a.claims.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      </Section>

      {/* Laws are shown exactly as the document names them. Nothing here is
          retrieved or verified against the statute index - that is what the
          Ask surface is for, and blurring the two would make an unverified
          section number look like a checked citation. */}
      <Section title="Laws and sections mentioned" count={a.laws.length}>
        <ul className="di-list">
          {a.laws.map((l, i) => (
            <li key={i}>
              <strong>
                {[l.act, l.section].filter(Boolean).join(' \u2014 ')}
              </strong>
              {l.context && <span className="di-sub">{l.context}</span>}
            </li>
          ))}
        </ul>
        <p className="di-fine">As named in the document. Not verified against the statute index.</p>
      </Section>

      <Section title="Important clauses" count={a.clauses.length}>
        <ul className="di-list">
          {a.clauses.map((c, i) => (
            <li key={i}>
              <strong>{c.heading || 'Clause'}</strong>
              <span className="di-kind">{c.kind}</span>
              <span className="di-sub">{c.summary}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Amounts" count={a.amounts.length}>
        <ul className="di-amounts">
          {a.amounts.map((m, i) => (
            <li key={i}>
              <strong>{m.amount}</strong>
              <span>{m.purpose}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Key facts" count={a.key_facts.length}>
        <ul className="di-bullets">
          {a.key_facts.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

/* --------------------------------------------------------- questions tab */

function QuestionsView({ state, onReload, onInsertText }) {
  const [picked, setPicked] = useState(() => new Set());
  const [copied, setCopied] = useState(false);

  const questions = state.questions?.questions || [];
  const known = state.questions?.already_known || [];

  // A regenerated list is a different list - a selection made against the
  // old one would silently point at the wrong questions.
  useEffect(() => {
    setPicked(new Set());
  }, [state.questions]);

  const selected = useMemo(
    () => questions.filter((_, i) => picked.has(i)),
    [questions, picked]
  );

  const composed = useMemo(() => {
    if (!selected.length) return '';
    if (selected.length === 1) return selected[0].question;
    return selected.map((q, i) => `${i + 1}. ${q.question}`).join('\n');
  }, [selected]);

  if (state.questionsLoading) {
    return <Spinner label={'Working out what is still missing\u2026'} />;
  }
  if (state.questionsError) {
    return <ErrorNote message={state.questionsError} onRetry={onReload} />;
  }
  if (!state.questions) return null;

  function toggle(i) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(composed);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch (_) {
      /* Clipboard is blocked in some browsers over http - the Insert
         button does the same job without it. */
    }
  }

  return (
    <div className="di-stack">
      <div className="di-head-row">
        <p className="di-lede">
          Built from this document and what has already been said in the chat.
        </p>
        <span className="di-spacer" />
        <button type="button" className="di-link" onClick={onReload}>
          Re-run
        </button>
      </div>

      {!questions.length && (
        <div className="di-note">
          Nothing further to ask from this document. Re-run after the client
          replies and it will look again.
        </div>
      )}

      <ul className="di-questions">
        {questions.map((q, i) => (
          <li key={i} className={`di-q ${picked.has(i) ? 'picked' : ''}`}>
            <button
              type="button"
              className="di-check"
              onClick={() => toggle(i)}
              aria-pressed={picked.has(i)}
              aria-label={picked.has(i) ? 'Deselect question' : 'Select question'}
            >
              {picked.has(i) ? I.check : null}
            </button>
            <div className="di-q-body">
              <div className="di-q-top">
                <span className={`di-sev ${q.priority}`}>{severityLabel(q.priority)}</span>
                <span className="di-cat">{q.category}</span>
              </div>
              <p className="di-q-text">{q.question}</p>
              {q.gap && (
                <p className="di-q-gap">
                  <span className="di-gap-ic">{I.warn}</span>
                  {q.gap}
                </p>
              )}
              {q.why && <p className="di-q-why">{q.why}</p>}
              <div className="di-q-actions">
                <button
                  type="button"
                  className="di-link"
                  onClick={() => onInsertText?.(q.question)}
                >
                  Insert into message
                </button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      {!!known.length && (
        <Section title="Already answered in this chat" count={known.length}>
          <ul className="di-bullets muted">
            {known.map((k, i) => (
              <li key={i}>{k}</li>
            ))}
          </ul>
        </Section>
      )}

      {!!selected.length && (
        <div className="di-sticky-actions">
          <button
            type="button"
            className="di-btn primary"
            onClick={() => onInsertText?.(composed)}
          >
            Insert {selected.length} into message
          </button>
          <button type="button" className="di-btn" onClick={copy}>
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- ask tab */

function AskView({ state, onAsk }) {
  const [draft, setDraft] = useState('');
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [state.chat, state.asking]);

  function submit(text) {
    const question = (text ?? draft).trim();
    if (!question || state.asking) return;
    onAsk(question);
    setDraft('');
  }

  return (
    <div className="di-ask">
      <div className="di-ask-thread">
        {!state.chat.length && (
          <div className="di-ask-intro">
            <p>
              Ask about this document. Answers come from the file itself, and
              say so when something is not in it.
            </p>
            <div className="di-chips">
              {ASK_SUGGESTIONS.map((s) => (
                <button
                  type="button"
                  key={s}
                  className="di-chip"
                  onClick={() => submit(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {state.chat.map((turn, i) =>
          turn.role === 'user' ? (
            <div className="di-ask-you" key={i}>
              {turn.content}
            </div>
          ) : (
            <div className="di-ask-ai" key={i}>
              <span className={`di-source ${turn.found_in_document ? 'found' : 'absent'}`}>
                {turn.found_in_document ? 'From the document' : 'Not in this document'}
              </span>
              <p className="di-ask-text">{turn.answer}</p>
              {!!turn.evidence?.length && (
                <ul className="di-evidence">
                  {turn.evidence.map((e, j) => (
                    <li key={j}>{e}</li>
                  ))}
                </ul>
              )}
              {turn.note && <p className="di-ask-note">{turn.note}</p>}
            </div>
          )
        )}

        {state.asking && <Spinner label={'Reading the document\u2026'} />}
        {state.askError && <ErrorNote message={state.askError} />}
        <div ref={endRef} />
      </div>

      <div className="di-ask-dock">
        <textarea
          className="di-ask-input"
          rows={1}
          value={draft}
          placeholder={'Ask about this document\u2026'}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          aria-label="Ask about this document"
        />
        <button
          type="button"
          className="di-ask-send"
          onClick={() => submit()}
          disabled={state.asking || !draft.trim()}
          aria-label="Send"
        >
          {state.asking ? <span className="spinner" /> : I.send}
        </button>
      </div>
    </div>
  );
}