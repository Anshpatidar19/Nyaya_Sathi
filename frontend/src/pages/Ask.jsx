import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import {
  askQuestion,
  createDraft,
  fetchDraftTypes,
  fetchHistory,
  reviewDocument,
} from '../api';

const MODES = [
  { id: 'ask', label: 'Ask', icon: '💬' },
  { id: 'draft', label: 'Draft', icon: '📝' },
  { id: 'review', label: 'Review', icon: '🔍' },
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

export default function Ask() {
  const { token, user, logout } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState('ask');
  const [input, setInput] = useState('');
  const [docType, setDocType] = useState('');
  const [types, setTypes] = useState([]);
  const [result, setResult] = useState(null);
  const [pendingPrompt, setPendingPrompt] = useState('');
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const threadEndRef = useRef(null);

  useEffect(() => {
    fetchHistory(token).then(setHistory).catch(() => {});
    fetchDraftTypes(token).then(setTypes).catch(() => {});
  }, [token]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [result, loading]);

  const started = loading || !!result || !!error;

  function switchMode(next) {
    setMode(next);
    setResult(null);
    setError('');
  }

  function startNew() {
    switchMode('ask');
    setInput('');
    setDocType('');
  }

  function handleLogout() {
    logout();
    navigate('/');
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!input.trim()) return;
    const prompt = input;
    setError('');
    setLoading(true);
    setCopied(false);
    setPendingPrompt(prompt);
    setInput('');

    try {
      if (mode === 'ask') {
        const data = await askQuestion(token, { question: prompt, state: user?.state });
        setResult({ kind: 'ask', ...data, prompt });
        setHistory(await fetchHistory(token));
      } else if (mode === 'draft') {
        const data = await createDraft(token, {
          doc_type: docType || null,
          instructions: prompt,
          details: null,
        });
        setResult({ kind: 'draft', ...data, prompt });
      } else {
        const data = await reviewDocument(token, {
          document_text: prompt,
          doc_type: docType || null,
          context: null,
        });
        setResult({ kind: 'review', ...data, prompt });
      }
    } catch (err) {
      setError(err.message);
      setInput(prompt);
    } finally {
      setLoading(false);
    }
  }

  function copyDraft() {
    if (!result?.body) return;
    navigator.clipboard.writeText(result.body).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
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
        <Link to="/" className="logo">
          <span className="mark">न्या</span> Nyaya Sathi
        </Link>
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
        <aside className="ask-sidebar">
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
                className="history-item"
                key={h.id}
                onClick={() => {
                  switchMode('ask');
                  setInput(h.question);
                }}
              >
                {h.question}
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

              <div className="ask-mode-pills" role="tablist">
                {MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    role="tab"
                    aria-selected={mode === m.id}
                    className={`mode-btn ${mode === m.id ? 'active' : ''}`}
                    onClick={() => switchMode(m.id)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>

              <form className="ask-input-card" onSubmit={handleSubmit}>
                {docSelect}
                {mode === 'review' ? (
                  <textarea
                    className="ask-input-textarea"
                    rows={5}
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
              <div className="mode-switch" role="tablist">
                {MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    role="tab"
                    aria-selected={mode === m.id}
                    className={`mode-btn ${mode === m.id ? 'active' : ''}`}
                    onClick={() => switchMode(m.id)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>

              <div className="ask-thread">
                <div className="ask-user-bubble">{pendingPrompt}</div>

                {error && <div className="form-error">{error}</div>}

                {loading && (
                  <div className="ask-thinking">
                    <span className="spinner" /> Thinking through this…
                  </div>
                )}

                {result?.kind === 'ask' && <AskResult data={result} />}
                {result?.kind === 'draft' && (
                  <DraftResult data={result} onCopy={copyDraft} copied={copied} />
                )}
                {result?.kind === 'review' && <ReviewResult data={result} />}
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
      {data.body.split(/\n{2,}/).map((p, i) => (
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
  const counts = data.flags.reduce((a, f) => ({ ...a, [f.severity]: (a[f.severity] || 0) + 1 }), {});
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

      {data.flags.map((f, i) => (
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