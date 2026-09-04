import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import {
  askQuestion,
  createDraft,
  deleteConversation,
  fetchConversation,
  fetchConversations,
  fetchDraftTypes,
  reviewDocument,
} from '../api';

/* Inline SVG icons. Emoji render differently on every OS and read as
   decoration rather than interface, so these are stroked line icons. */
const Icon = {
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
  trash: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
         strokeLinejoin="round">
      <path d="M4 6h12M8 6V4h4v2M6 6l.7 10h6.6L15 6" />
    </svg>
  ),
};

const MODES = [
  { id: 'ask', label: 'Ask', icon: Icon.ask },
  { id: 'draft', label: 'Draft', icon: Icon.draft },
  { id: 'review', label: 'Review', icon: Icon.review },
];

const PLACEHOLDERS = {
  ask: 'Ask a legal question, e.g. "can my landlord evict me without notice?"',
  draft: 'Describe what you need, e.g. "Rakesh gave me a cheque for 85,000 that bounced on 12 August 2026"',
  review: "Paste the contract or notice you were sent, and I'll flag the risky clauses.",
};

const WELCOME_SUB = {
  ask: 'Ask any legal question, in plain language.',
  draft: 'Tell me what document you need, and I’ll draft it.',
  review: 'Paste a contract or notice and I’ll flag the risky parts.',
};

const CHIPS = {
  ask: [
    { label: 'Anticipatory Bail', fill: 'How do I apply for anticipatory bail?' },
    { label: 'Cheque Bounce (NI Act)', fill: 'My cheque bounced — what should I do now?' },
    { label: 'Property Dispute', fill: 'How do I resolve a property boundary dispute with my neighbour?' },
    { label: 'Rent Control', fill: 'Can my landlord increase my rent without notice?' },
    { label: 'FIR Quashing', fill: 'How can I get an FIR quashed?' },
    { label: 'Divorce Procedure', fill: 'What is the procedure for a mutual consent divorce?' },
    { label: 'POCSO Act', fill: 'What does the POCSO Act cover?' },
    { label: 'Consumer Forum', fill: 'How do I file a complaint in the consumer forum?' },
  ],
  draft: [
    { label: 'RTI Application', fill: 'Draft an RTI application to the municipal corporation asking how much was spent on road repairs in my ward.' },
    { label: 'Legal Notice', fill: 'Draft a legal notice to a tenant who has stopped paying rent.' },
    { label: 'Consumer Complaint', fill: 'Draft a consumer complaint against an online seller who sent a defective product.' },
    { label: 'Rent Agreement', fill: 'Draft an 11-month rent agreement for a 2BHK flat.' },
  ],
  review: [],
};

const STORAGE_KEY = 'ns_active_conversation';

export default function Ask() {
  const { token, user, logout } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState('ask');
  const [input, setInput] = useState('');
  const [docType, setDocType] = useState('');
  const [types, setTypes] = useState([]);
  const [turns, setTurns] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [history, setHistory] = useState([]);
  // Visible by default, like ChatGPT. The choice is remembered.
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem('ns_sidebar') !== 'closed'
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [copiedIdx, setCopiedIdx] = useState(null);
  const threadEndRef = useRef(null);

  useEffect(() => {
    if (!token) return;
    refreshHistory();
    fetchDraftTypes(token).then(setTypes).catch(() => {});

    // Restore the last thread so a refresh doesn't wipe the screen.
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) loadConversation(Number(saved));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns, loading]);

  useEffect(() => {
    localStorage.setItem('ns_sidebar', sidebarOpen ? 'open' : 'closed');
  }, [sidebarOpen]);

  function refreshHistory() {
    fetchConversations(token).then(setHistory).catch(() => {});
  }

  async function loadConversation(id) {
    try {
      const data = await fetchConversation(token, id);
      setConversationId(data.id);
      setMode(data.mode || 'ask');
      setTurns(
        data.turns.map((t) => ({
          kind: t.mode || 'ask',
          prompt: t.question,
          ...(t.payload || { title: t.answer_title, body: t.answer_body, citations: [], next_steps: [] }),
        }))
      );
      localStorage.setItem(STORAGE_KEY, String(data.id));
      setError('');
    } catch {
      localStorage.removeItem(STORAGE_KEY);   // thread was deleted
    }
  }

  const started = loading || turns.length > 0 || !!error;

  function switchMode(next) {
    setMode(next);
    setTurns([]);
    setConversationId(null);
    setError('');
    localStorage.removeItem(STORAGE_KEY);
  }

  function startNew() {
    switchMode('ask');
    setInput('');
    setDocType('');
  }

  async function removeConversation(e, id) {
    e.stopPropagation();
    try {
      await deleteConversation(token, id);
      if (id === conversationId) startNew();
      refreshHistory();
    } catch {
      setError('Could not delete that conversation.');
    }
  }

  function handleLogout() {
    logout();
    navigate('/');
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!input.trim() || loading) return;
    const prompt = input;
    setError('');
    setLoading(true);
    setInput('');
    setTurns((t) => [...t, { kind: 'pending', prompt }]);

    try {
      let turn;
      if (mode === 'ask') {
        const data = await askQuestion(token, {
          question: prompt,
          state: user?.state,
          conversation_id: conversationId,
        });
        if (data.conversation_id) {
          setConversationId(data.conversation_id);
          localStorage.setItem(STORAGE_KEY, String(data.conversation_id));
        }
        turn = { kind: 'ask', ...data, prompt };
        refreshHistory();
      } else if (mode === 'draft') {
        const data = await createDraft(token, {
          doc_type: docType || null,
          instructions: prompt,
          details: null,
        });
        turn = { kind: 'draft', ...data, prompt };
      } else {
        const data = await reviewDocument(token, {
          document_text: prompt,
          doc_type: docType || null,
          context: null,
        });
        turn = { kind: 'review', ...data, prompt };
      }
      setTurns((t) => [...t.slice(0, -1), turn]);
    } catch (err) {
      setTurns((t) => t.slice(0, -1));
      setError(err.message);
      setInput(prompt);
    } finally {
      setLoading(false);
    }
  }

  function copyDraft(body, idx) {
    if (!body) return;
    navigator.clipboard.writeText(body).then(() => {
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx(null), 2000);
    });
  }

  const grouped = types.reduce((acc, t) => {
    (acc[t.category] = acc[t.category] || []).push(t);
    return acc;
  }, {});

  const docSelect = mode !== 'ask' && (
    <select className="doc-select" value={docType} onChange={(e) => setDocType(e.target.value)}>
      <option value="">
        {mode === 'draft' ? 'Let Nyaya Sathi decide the format' : 'Document type (optional)'}
      </option>
      {Object.keys(grouped).sort().map((cat) => (
        <optgroup label={cat} key={cat}>
          {grouped[cat].map((t) => (
            <option value={t.id} key={t.id}>
              {t.name}{t.needs_advocate ? ' — needs advocate review' : ''}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );

  return (
    <div className="ask-app">
      <header className="ask-topbar">
        <div className="ask-topbar-left">
          <button
            type="button"
            className="sidebar-toggle"
            onClick={() => setSidebarOpen((o) => !o)}
            aria-label={sidebarOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={sidebarOpen}
          >
            {sidebarOpen ? Icon.close : Icon.menu}
          </button>
          <Link to="/" className="logo">
            <span className="mark">न्या</span> Nyaya Sathi
          </Link>
        </div>
        <div className="ask-topbar-right">
          <span className="user-chip">
            <span className="avatar">{user?.name?.[0]?.toUpperCase() || 'U'}</span>
            {user?.name?.split(' ')[0]}
          </span>
          <button type="button" className="btn btn-ghost" onClick={handleLogout}>Log out</button>
        </div>
      </header>

      <main className="ask-shell">
      <div className="ask-layout">
        <aside className={`ask-sidebar ${sidebarOpen ? '' : 'collapsed'}`}>
          <button type="button" className="new-chat-btn" onClick={startNew}>
            <span className="plus-ic">+</span> New question
          </button>

          <nav className="ask-nav" role="tablist">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                role="tab"
                aria-selected={mode === m.id}
                className={`ask-nav-item ${mode === m.id ? 'active' : ''}`}
                onClick={() => switchMode(m.id)}
              >
                <span className="ask-nav-icon">{m.icon}</span>
                {m.label}
              </button>
            ))}
          </nav>

          <div className="ask-sidebar-divider" />

          <h4>Recents</h4>
          <div className="ask-history-list">
            {history.length === 0 && (
              <div className="history-empty">Nothing asked yet — try a question on the right.</div>
            )}
            {history.map((h) => (
              <div
                className={`history-item ${h.id === conversationId ? 'active' : ''}`}
                key={h.id}
                onClick={() => loadConversation(h.id)}
              >
                <span className="history-item-text">{h.title}</span>
                <button
                  type="button"
                  className="history-item-del"
                  onClick={(e) => removeConversation(e, h.id)}
                  aria-label="Delete conversation"
                >
                  {Icon.trash}
                </button>
              </div>
            ))}
          </div>

          {user && (
            <div className="ask-sidebar-user">
              <span className="avatar">{user.name?.[0]?.toUpperCase() || 'U'}</span>
              <div className="ask-sidebar-user-text">
                <div className="name">{user.name}</div>
                {user.state && <div className="sub">{user.state}</div>}
              </div>
            </div>
          )}
        </aside>

        <div className="ask-main">
          {!started ? (
            <div className="ask-welcome">
              <h1>Welcome to Nyaya Sathi</h1>
              <p className="ask-welcome-sub">{WELCOME_SUB[mode]}</p>

              <form className="ask-input-card" onSubmit={handleSubmit}>
                {docSelect}
                {mode === 'review' ? (
                  <textarea
                    className="ask-input-textarea"
                    rows={3}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder={PLACEHOLDERS.review}
                  />
                ) : (
                  <textarea
                    className="ask-input-textarea"
                    rows={2}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        handleSubmit(e);
                      }
                    }}
                    placeholder={PLACEHOLDERS[mode]}
                  />
                )}
                <div className="ask-input-actions">
                  <button type="button" className="attach-btn" disabled title="Attachments coming soon">+</button>
                  <button type="submit" className="send-btn" disabled={loading || !input.trim()}>
                    {loading ? <span className="spinner" /> : '↑'}
                  </button>
                </div>
              </form>

              {CHIPS[mode]?.length > 0 && (
                <div className="ask-chip-grid">
                  {CHIPS[mode].map((c) => (
                    <button
                      key={c.label}
                      type="button"
                      className="ask-chip"
                      onClick={() => setInput(c.fill)}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              )}

              <p className="ask-disclaimer">
                Nyaya Sathi is AI and can make mistakes. Please verify legal advice with a qualified advocate.
              </p>
            </div>
          ) : (
            <div className="ask-conversation">
              <div className="ask-thread">
                {turns.map((t, i) => (
                  <div className="ask-turn" key={i}>
                    <div className="ask-user-bubble">{t.prompt}</div>
                    {t.kind === 'pending' ? (
                      <div className="ask-thinking">
                        <span className="spinner" /> Thinking through this…
                      </div>
                    ) : (
                      <>
                        {t.kind === 'ask' && <AskResult data={t} />}
                        {t.kind === 'draft' && (
                          <DraftResult
                            data={t}
                            onCopy={() => copyDraft(t.body, i)}
                            copied={copiedIdx === i}
                          />
                        )}
                        {t.kind === 'review' && <ReviewResult data={t} />}
                      </>
                    )}
                  </div>
                ))}

                {error && <div className="form-error">{error}</div>}
                <div ref={threadEndRef} />
              </div>

              <form className="ask-form-dock" onSubmit={handleSubmit}>
                {docSelect}
                <div className="ask-dock-row">
                  {mode === 'review' ? (
                    <textarea
                      className="review-input"
                      rows={3}
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      placeholder={PLACEHOLDERS.review}
                    />
                  ) : (
                    <input
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      placeholder={PLACEHOLDERS[mode]}
                    />
                  )}
                  <button className="btn btn-primary" type="submit" disabled={loading || !input.trim()}>
                    {loading ? <span className="spinner" /> : mode === 'ask' ? 'Ask' : mode === 'draft' ? 'Draft it' : 'Review it'}
                  </button>
                </div>
              </form>
            </div>
          )}
        </div>
      </div>
      </main>
    </div>
  );
}

function Citations({ items, label = 'Sources' }) {
  if (!items?.length) return null;
  return (
    <div className="cite-list">
      <div className="cite-label">{label}</div>
      {items.map((c, i) => {
        const href = c.url || (c.docid ? `https://indiankanoon.org/doc/${c.docid}/` : null);
        const inner = (
          <>
            <span className="num">{i + 1}</span>
            <div className="cite-text">
              <div className="cite-title">{c.title}</div>
              <div className="cite-sub">{c.source}</div>
              {c.snippet && <div className="cite-snippet">{c.snippet}</div>}
            </div>
            {href && <span className="cite-arrow" aria-hidden="true">↗</span>}
          </>
        );
        return href ? (
          <a className="cite cite-link" key={i} href={href} target="_blank" rel="noopener noreferrer">
            {inner}
          </a>
        ) : (
          <div className="cite" key={i}>{inner}</div>
        );
      })}
    </div>
  );
}

function AskResult({ data }) {
  return (
    <div className="demo-card">
      <div className="demo-topbar">
        <div className="demo-brand"><span className="sq">न्या</span> Research</div>
        <div className="status-chip live"><span className="dot" /> Answered live</div>
      </div>
      <h4 className="answer-title">{data.title}</h4>
      {(data.body || '').split(/\n{2,}/).map((p, i) => (
        <p className="answer-body" key={i}>{p}</p>
      ))}
      <Citations items={data.citations} />
      {data.next_steps?.length > 0 && (
        <div className="demo-steps">
          <div className="label">Suggested next steps</div>
          {data.next_steps.map((s, i) => (
            <div className="step-row" key={i}><span className="num">{i + 1}</span> {s}</div>
          ))}
        </div>
      )}
      <p className="answer-disclaimer">
        Legal information drawn from public statutes and judgments, not legal advice.
        Check the linked sources or speak to a lawyer before you act.
      </p>
    </div>
  );
}

function DraftResult({ data, onCopy, copied }) {
  return (
    <div className="demo-card">
      <div className="demo-topbar">
        <div className="demo-brand"><span className="sq">न्या</span> Draft</div>
        <button type="button" className="copy-btn" onClick={onCopy}>
          {copied ? 'Copied' : 'Copy text'}
        </button>
      </div>

      {data.needs_advocate && (
        <div className="advocate-warning">
          This is a court document. Have an advocate settle it before filing — a
          defective filing can be rejected or damage your case.
        </div>
      )}

      <h4 className="answer-title">{data.title}</h4>
      <pre className="draft-body">{data.body}</pre>

      {data.missing_information?.length > 0 && (
        <div className="demo-steps missing">
          <div className="label">You still need to fill in</div>
          {data.missing_information.map((m, i) => (
            <div className="step-row" key={i}><span className="num">{i + 1}</span> {m}</div>
          ))}
        </div>
      )}

      {data.notes?.length > 0 && (
        <div className="demo-steps">
          <div className="label">Before you send this</div>
          {data.notes.map((n, i) => (
            <div className="step-row" key={i}><span className="num">{i + 1}</span> {n}</div>
          ))}
        </div>
      )}

      <Citations items={data.citations} label="Legal basis" />

      <p className="answer-disclaimer">
        A first draft for you to review and edit, not a filed or executed document.
      </p>
    </div>
  );
}

function ReviewResult({ data }) {
  const counts = (data.flags || []).reduce((a, f) => ({ ...a, [f.severity]: (a[f.severity] || 0) + 1 }), {});
  return (
    <div className="demo-card">
      <div className="demo-topbar">
        <div className="demo-brand"><span className="sq">न्या</span> Review</div>
        <div className={`risk-chip risk-${data.risk_level}`}>
          {data.risk_level} risk
        </div>
      </div>

      <p className="answer-body">{data.summary}</p>

      {data.truncated && (
        <div className="advocate-warning">
          This document was long, so only the first part was reviewed.
        </div>
      )}

      <div className="flag-counts">
        {['high', 'medium', 'low'].map((s) =>
          counts[s] ? <span className={`flag-pill sev-${s}`} key={s}>{counts[s]} {s}</span> : null
        )}
      </div>

      {(data.flags || []).map((f, i) => (
        <div className={`flag sev-${f.severity}`} key={i}>
          <div className="flag-head">
            <span className={`flag-pill sev-${f.severity}`}>{f.severity}</span>
            <span className="flag-basis">
              {f.basis === 'statute' ? 'Legal issue' : 'Drafting issue'}
            </span>
          </div>
          <blockquote className="flag-clause">{f.clause}</blockquote>
          <p className="flag-issue">{f.issue}</p>
          <p className="flag-suggestion"><strong>Suggested change:</strong> {f.suggestion}</p>
          {f.citation && (
            <a
              className="flag-cite"
              href={f.citation.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {f.citation.title} ↗
            </a>
          )}
        </div>
      ))}

      {data.missing_clauses?.length > 0 && (
        <div className="demo-steps missing">
          <div className="label">Clauses this document is missing</div>
          {data.missing_clauses.map((m, i) => (
            <div className="step-row" key={i}><span className="num">{i + 1}</span> {m}</div>
          ))}
        </div>
      )}

      <p className="answer-disclaimer">
        An automated first pass, not a lawyer's opinion. Have anything you're about
        to sign checked by an advocate.
      </p>
    </div>
  );
}