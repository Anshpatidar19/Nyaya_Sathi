/* Research inside one matter.

   Opened from a matter's Research tab. It looks like Ask, but it is not a
   window onto Ask:

   - every question goes out with this matter's id, so the backend answers
     it against the case file (facts, court, side, hearing notes, papers)
     and files the thread under this matter;
   - the thread list shows only this matter's research, and Ask's own
     history leaves these threads out;
   - documents come from this matter - uploads here are filed to it, and
     papers already on the matter can be attached without re-uploading;
   - a thread that belongs to a different matter is refused, not opened.

   The answer card, streaming card and progress stepper are the same
   components Ask uses, so both surfaces behave identically where they
   overlap. */

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { AppNav, AppSidebarUser, AppTopbar } from '../components/AppShell';
import StreamingAnswer from '../components/StreamingAnswer';
import { AskResult, DraftResult, ReviewResult } from './Ask';
import {
  askQuestionStream,
  deleteConversation,
  fetchConversation,
  fetchMatter,
  invalidateReads,
  uploadMatterDocument,
} from '../api';
import '../thinking.css';
import '../matterresearch.css';

const ACCEPT =
  '.pdf,.doc,.docx,.txt,.jpg,.jpeg,.png,.webp,.heic,.heif,' +
  'application/pdf,application/msword,text/plain,' +
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document,' +
  'image/jpeg,image/png,image/webp,image/heic,image/heif';

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const DOCK_MAX_H = 200;

const MODE_TAG = { ask: 'Ask', draft: 'Draft', review: 'Review', argue: 'Arguments' };

function autoGrow(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, DOCK_MAX_H)}px`;
}

// Starting points written for THIS matter, so the first question is one
// click away and already scoped.
function suggestionsFor(matter) {
  const side = matter?.side ? `the ${matter.side.toLowerCase()}` : 'my client';
  return [
    'Which provisions of law apply to this matter?',
    `What defences or arguments are available to ${side}?`,
    'What recent case law is relevant to this matter?',
    'What should I prepare before the next hearing?',
  ];
}

const Icons = {
  clip: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.5 9.2l-5 5a3.1 3.1 0 0 1-4.4-4.4l6-6a2.1 2.1 0 0 1 3 3l-6 6a1.1 1.1 0 0 1-1.5-1.5l5.2-5.2" />
    </svg>
  ),
  file: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 2.5h6l4 4v11H5z" />
      <path d="M11 2.5v4h4" />
    </svg>
  ),
  trash: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
         strokeLinejoin="round">
      <path d="M4 6h12M8 6V4h4v2M6 6l.7 10h6.6L15 6" />
    </svg>
  ),
};

export default function MatterResearch() {
  const { id } = useParams();
  const matterId = Number(id);
  const [searchParams, setSearchParams] = useSearchParams();
  const { token, user, isAdvocate, loading: authLoading } = useAuth();
  const navigate = useNavigate();

  const [matter, setMatter] = useState(null);
  const [pageLoading, setPageLoading] = useState(true);
  const [pageError, setPageError] = useState('');

  const [conversationId, setConversationId] = useState(null);
  const [threadMode, setThreadMode] = useState('ask');
  const [turns, setTurns] = useState([]);
  const [input, setInput] = useState('');
  const [attachment, setAttachment] = useState(null);   // {id, filename}
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [copiedIdx, setCopiedIdx] = useState(null);
  const [docMenuOpen, setDocMenuOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem('ns_sidebar') !== 'closed'
  );

  const fileInputRef = useRef(null);
  const threadEndRef = useRef(null);
  // A thread load that resolves after the user has started typing into a
  // new one must be dropped, not applied over their question.
  const loadSeq = useRef(0);

  useEffect(() => {
    if (!authLoading && !isAdvocate) navigate('/ask', { replace: true });
  }, [authLoading, isAdvocate, navigate]);

  useEffect(() => {
    localStorage.setItem('ns_sidebar', sidebarOpen ? 'open' : 'closed');
  }, [sidebarOpen]);

  useEffect(() => {
    if (authLoading || !token || !isAdvocate) return;
    reloadMatter({ first: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, token, isAdvocate, matterId]);

  // ?c=<id> opens a thread. Read on mount and whenever the matter's own
  // Research tab links in with a different one.
  const urlThread = Number(searchParams.get('c')) || null;
  useEffect(() => {
    if (!matter) return;
    if (urlThread && urlThread !== conversationId) openThread(urlThread);
    if (!urlThread && conversationId && !loading) resetThread();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matter?.id, urlThread]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [turns]);

  async function reloadMatter({ first = false } = {}) {
    try {
      invalidateReads('/matters');
      const data = await fetchMatter(token, matterId, { fresh: true });
      setMatter(data);
      setPageError('');
    } catch (err) {
      if (first) setPageError(err.message);
    } finally {
      if (first) setPageLoading(false);
    }
  }

  function resetThread() {
    loadSeq.current += 1;
    setConversationId(null);
    setThreadMode('ask');
    setTurns([]);
    setError('');
    clearAttachment();
  }

  function startNew() {
    resetThread();
    setInput('');
    setSearchParams({}, { replace: false });
  }

  async function openThread(cid) {
    const seq = ++loadSeq.current;
    setError('');
    try {
      const data = await fetchConversation(token, cid);
      if (seq !== loadSeq.current) return;

      // Only this matter's threads open here. A hand-edited ?c= pointing at
      // another case's research is refused rather than quietly shown.
      if (data.matter_id !== matterId) {
        setTurns([]);
        setConversationId(null);
        setError('That research thread belongs to a different matter.');
        setSearchParams({}, { replace: true });
        return;
      }

      setConversationId(data.id);
      setThreadMode(data.mode || 'ask');
      setTurns(
        data.turns.map((t) => ({
          kind: t.mode || 'ask',
          prompt: t.question,
          ...(t.payload || {
            title: t.answer_title, body: t.answer_body, citations: [], next_steps: [],
          }),
          query_log_id: t.id,
        }))
      );
    } catch {
      if (seq !== loadSeq.current) return;
      setError('That research thread could not be opened. It may have been deleted.');
      setSearchParams({}, { replace: true });
    }
  }

  async function removeThread(e, cid) {
    e.stopPropagation();
    if (!window.confirm('Delete this research thread?')) return;
    try {
      await deleteConversation(token, cid);
      if (cid === conversationId) startNew();
      reloadMatter();
    } catch {
      setError('Could not delete that thread.');
    }
  }

  function clearAttachment() {
    setAttachment(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  async function handleFilePicked(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      setError('That file is larger than 10 MB. Try a smaller one.');
      clearAttachment();
      return;
    }
    setError('');
    setUploading(true);
    try {
      // Filed to this matter on upload, so it shows under the matter's
      // Documents tab too and can be re-attached later without uploading.
      const doc = await uploadMatterDocument(token, matterId, file);
      setAttachment({ id: doc.id, filename: doc.filename });
      reloadMatter();
    } catch (err) {
      setError(err.message);
      clearAttachment();
    } finally {
      setUploading(false);
    }
  }

  function pickMatterDocument(doc) {
    setAttachment({ id: doc.id, filename: doc.filename });
    setDocMenuOpen(false);
  }

  const canSubmit = !loading && !uploading && (!!input.trim() || !!attachment);

  async function handleSubmit(e, preset) {
    e?.preventDefault?.();
    const prompt = (preset ?? input).trim();
    if (loading || uploading || (!prompt && !attachment)) return;

    // Draft and Review threads filed here are shown read-only. A question
    // typed under one starts a fresh Ask thread on the same matter rather
    // than being appended to a thread of a different kind.
    const continueId = threadMode === 'ask' ? conversationId : null;
    if (!continueId) {
      setConversationId(null);
      setThreadMode('ask');
    }

    const sentFile = attachment;
    const bubble = prompt || sentFile?.filename || '';
    loadSeq.current += 1;   // any thread load in flight is now stale

    setError('');
    setLoading(true);
    setInput('');
    setTurns((t) => [
      ...(continueId ? t : []),
      { kind: 'streaming', prompt: bubble, file: sentFile?.filename, body: '', status: null },
    ]);

    const patchLast = (fn) =>
      setTurns((t) => {
        if (!t.length) return t;
        const next = [...t];
        next[next.length - 1] = fn(next[next.length - 1]);
        return next;
      });

    try {
      const data = await askQuestionStream(
        token,
        {
          question: prompt,
          state: user?.state,
          conversation_id: continueId,
          document_id: sentFile?.id,
          matter_id: matterId,
        },
        {
          onStatus: (status) =>
            patchLast((t0) => (t0.kind === 'streaming' ? { ...t0, status } : t0)),
          onDelta: (text) =>
            patchLast((t0) => ({ ...t0, body: (t0.body || '') + text })),
          onRevised: (body) => patchLast((t0) => ({ ...t0, body, revised: true })),
          onDone: (d) => {
            patchLast((t0) => ({
              ...t0, kind: 'ask', ...d, prompt: bubble, file: sentFile?.filename,
            }));
            setLoading(false);
          },
        },
      );

      patchLast(() => ({ kind: 'ask', ...data, prompt: bubble, file: sentFile?.filename }));
      if (data.conversation_id) {
        setConversationId(data.conversation_id);
        setThreadMode('ask');
        if (data.conversation_id !== urlThread) {
          // Keep the address bar on the thread, so a refresh reopens it.
          // conversationId is set in the same render, so the ?c= effect
          // sees the thread as already open and does not refetch it.
          setSearchParams({ c: String(data.conversation_id) }, { replace: true });
        }
      }
      clearAttachment();
      reloadMatter();   // the thread list must show the new thread now
    } catch (err) {
      if (err.terminal) {
        patchLast(() => ({
          kind: 'rejected', prompt: bubble, file: sentFile?.filename, message: err.message,
        }));
        clearAttachment();
      } else {
        setTurns((t) => t.slice(0, -1));
        setError(err.message);
        setInput(prompt);
      }
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

  if (pageLoading || !matter) {
    return (
      <div className="ask-app">
        <AppTopbar sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((o) => !o)} />
        <main className="ask-shell">
          <div className="page-empty">
            {pageLoading ? (
              <span className="spinner spinner-dark" />
            ) : (
              <>
                <p>{pageError || "That matter doesn't exist, or isn't yours."}</p>
                <button type="button" className="btn btn-ghost" onClick={() => navigate('/matters')}>
                  Back to matters
                </button>
              </>
            )}
          </div>
        </main>
      </div>
    );
  }

  const threads = (matter.research || []).filter((c) => c.mode !== 'argue');
  const matterDocs = matter.documents || [];
  const started = turns.length > 0;
  const facts = [
    matter.client_name,
    matter.court,
    matter.side ? `For the ${matter.side}` : null,
    matter.case_number,
  ].filter(Boolean);

  const fileInput = (
    <input
      ref={fileInputRef}
      type="file"
      accept={ACCEPT}
      onChange={handleFilePicked}
      style={{ display: 'none' }}
    />
  );

  const attachControls = (
    <div className="mr-attach">
      <button
        type="button"
        className="attach-btn"
        onClick={() => fileInputRef.current?.click()}
        disabled={uploading || loading}
        title="Upload a document to this matter and ask about it"
        aria-label="Upload a document"
      >
        {uploading ? <span className="spinner spinner-dark" /> : Icons.clip}
      </button>
      {matterDocs.length > 0 && (
        <div className="mr-docpick">
          <button
            type="button"
            className="mr-docpick-btn"
            onClick={() => setDocMenuOpen((o) => !o)}
            disabled={loading}
            aria-expanded={docMenuOpen}
          >
            Matter papers ({matterDocs.length})
          </button>
          {docMenuOpen && (
            <div className="mr-docpick-menu" role="menu">
              {matterDocs.map((d) => (
                <button
                  type="button"
                  role="menuitem"
                  key={d.id}
                  className={`mr-docpick-item ${attachment?.id === d.id ? 'active' : ''}`}
                  onClick={() => pickMatterDocument(d)}
                >
                  {Icons.file}
                  <span>{d.filename}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );

  const attachChip = attachment && (
    <div className="attach-chip">
      <span className="attach-chip-ic">{Icons.file}</span>
      <span className="attach-chip-name">{attachment.filename}</span>
      <button type="button" className="attach-chip-x" onClick={clearAttachment} aria-label="Remove attachment">
        ×
      </button>
    </div>
  );

  const composerBox = (
    <div className="dock-box">
      {attachControls}
      <textarea
        className="dock-input"
        rows={1}
        value={input}
        ref={autoGrow}
        onChange={(e) => { setInput(e.target.value); autoGrow(e.target); }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSubmit(e);
          }
        }}
        placeholder={`Ask about ${matter.title}…`}
      />
      <button className="dock-send" type="submit" disabled={!canSubmit}>
        {loading ? <span className="spinner" /> : 'Send'}
      </button>
    </div>
  );

  return (
    <div className="ask-app ask-app-fill">
      <AppTopbar sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((o) => !o)} />

      <main className="ask-shell">
        <div className="ask-layout">
          <aside className={`ask-sidebar ${sidebarOpen ? '' : 'collapsed'}`}>
            <button
              type="button"
              className="new-chat-btn mr-back"
              onClick={() => navigate(`/matters/${matterId}`)}
            >
              ← Back to matter
            </button>
            <button type="button" className="new-chat-btn mr-new" onClick={startNew}>
              <span className="plus-ic">+</span> New research
            </button>

            <AppNav active="matters" />
            <div className="ask-sidebar-divider" />

            <h4>Research on this matter</h4>
            <div className="ask-history-list">
              {threads.length === 0 && (
                <div className="history-empty">No research on this matter yet.</div>
              )}
              {threads.map((c) => (
                <div
                  key={c.id}
                  className={`history-item ${c.id === conversationId ? 'active' : ''}`}
                  onClick={() => { if (!loading) setSearchParams({ c: String(c.id) }); }}
                >
                  <span className="history-item-text">
                    {c.mode !== 'ask' && <span className="mr-mode">{MODE_TAG[c.mode] || c.mode}</span>}
                    {c.title}
                  </span>
                  <button
                    type="button"
                    className="history-item-del"
                    onClick={(e) => removeThread(e, c.id)}
                    aria-label="Delete thread"
                  >
                    {Icons.trash}
                  </button>
                </div>
              ))}
            </div>

            <AppSidebarUser />
          </aside>

          <div className="ask-main mr-main">
            <div className="mr-context">
              <div className="mr-context-text">
                <span className="mr-context-label">Matter research</span>
                <strong className="mr-context-title">{matter.title}</strong>
                {facts.length > 0 && <span className="mr-context-facts">{facts.join(' · ')}</span>}
              </div>
              <span className="mr-scope-chip" title="Questions here are answered against this case file, and the threads stay filed under this matter.">
                Scoped to this matter
              </span>
            </div>

            {!started ? (
              <div className="ask-welcome mr-welcome">
                <h1>Research this matter</h1>
                <p className="ask-welcome-sub">
                  Answers take this case file into account — facts, court, the side you
                  appear for, hearing notes and papers — with the law still drawn only
                  from sources you can check.
                </p>

                <form className="ask-input-card" onSubmit={handleSubmit}>
                  {fileInput}
                  {attachChip}
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
                    placeholder={`Ask about ${matter.title}…`}
                  />
                  <div className="ask-input-actions">
                    {attachControls}
                    <span className="actions-spacer" />
                    <button type="submit" className="send-btn" disabled={!canSubmit}>
                      {loading ? <span className="spinner" /> : 'Send'}
                    </button>
                  </div>
                </form>

                <div className="ask-chip-grid">
                  {suggestionsFor(matter).map((q) => (
                    <button key={q} type="button" className="ask-chip" onClick={() => setInput(q)}>
                      {q}
                    </button>
                  ))}
                </div>

                {error && <div className="form-error">{error}</div>}

                <p className="ask-disclaimer">
                  Nyaya Sathi is AI and can make mistakes. Verify every authority before relying on it.
                </p>
              </div>
            ) : (
              <div className="ask-conversation">
                <div className="ask-thread">
                  {threadMode !== 'ask' && (
                    <div className="mr-readonly">
                      This {MODE_TAG[threadMode] || threadMode} thread is shown read-only.
                      A question sent below starts a new research thread on this matter.
                    </div>
                  )}

                  {turns.map((t, i) => (
                    <div className="ask-turn" key={i}>
                      <div className="ask-user-bubble">
                        {t.file && (
                          <span className="bubble-file">
                            {Icons.file} {t.file}
                          </span>
                        )}
                        {t.prompt !== t.file && t.prompt}
                      </div>
                      {t.kind === 'streaming' ? (
                        <StreamingAnswer body={t.body} status={t.status} brand="Matter research" />
                      ) : t.kind === 'rejected' ? (
                        <div className="ask-rejection">{t.message}</div>
                      ) : t.kind === 'draft' ? (
                        <DraftResult
                          data={t}
                          token={token}
                          onCopy={(text) => copyDraft(text ?? t.body, i)}
                          copied={copiedIdx === i}
                        />
                      ) : t.kind === 'review' ? (
                        <ReviewResult data={t} />
                      ) : (
                        <AskResult data={t} token={token} />
                      )}
                    </div>
                  ))}

                  {error && <div className="form-error">{error}</div>}
                  <div ref={threadEndRef} />
                </div>

                <form className="ask-form-dock" onSubmit={handleSubmit}>
                  {fileInput}
                  <div className="ask-dock-row">
                    {attachChip}
                    {composerBox}
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