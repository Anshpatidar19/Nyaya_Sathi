import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { fetchDocumentUrl, uploadDocument } from '../api';
import {
  fetchMessages,
  fetchThread,
  fetchThreads,
  markThreadRead,
  postMessage,
} from '../networkApi';
import { NetworkPage, refreshBadges } from '../components/AppShell';
import { useDocIntel } from '../components/DocIntel';
import {
  Avatar,
  DemoBadge,
  Empty,
  clockTime,
  dayLabel,
  parseUtc,
  timeAgo,
} from '../components/NetworkBits';

/* Two views, not two columns.

   This used to be a permanent 320px conversation rail beside the chat. With
   one or two conversations that rail was almost entirely empty, and it took
   a third of the width away from the thing people are actually here to read.

   So: /messages is the list, /messages/:id is the conversation, and the
   conversation gets the whole width. The list is one route away via the back
   button in the chat header.

   Polling, not WebSockets. The backend's message route takes `after_id`, so
   a poll is "anything in this thread above the last id I have" - one indexed
   lookup that usually returns nothing. Only the open view polls: the list
   does not poll while a conversation is open, and vice versa. */
const MESSAGE_POLL_MS = 3000;
const THREAD_POLL_MS = 12000;

const MAX_CHARS = 5000;

// Mirrors storage.ALLOWED_TYPES / MAX_BYTES on the backend - this only
// filters the OS picker and gives an early error, the server still enforces
// both for real.
const ACCEPT = '.pdf,.doc,.docx,.txt,.jpg,.jpeg,.png,.webp,.heic,.heif';
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

function formatBytes(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const ClipIcon = (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M14.5 9.2l-5 5a3.1 3.1 0 0 1-4.4-4.4l6-6a2.1 2.1 0 0 1 3 3l-6 6a1.1 1.1 0 0 1-1.5-1.5l5.2-5.2" />
  </svg>
);

const FileIcon = (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 2.5h6l4 4v11H5z" />
    <path d="M11 2.5v4h4" />
  </svg>
);

/* A sent or received file. Opens on click via a freshly signed URL - the
   stored path is private, so nothing is fetchable without one. Each card
   holds its own loading state rather than the whole conversation's, since
   opening one file shouldn't disable every other bubble on screen. */
function AttachmentCard({ attachment, token }) {
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');

  async function open() {
    if (opening) return;
    setOpening(true);
    setError('');
    try {
      const { url } = await fetchDocumentUrl(token, attachment.id);
      window.open(url, '_blank', 'noopener');
    } catch (err) {
      setError(err.message || "Couldn't open that file.");
    } finally {
      setOpening(false);
    }
  }

  return (
    <button
      type="button"
      className="nx-attach-card"
      onClick={open}
      disabled={opening}
      title={attachment.filename}
    >
      <span className="nx-attach-ic">{opening ? <span className="spinner" /> : FileIcon}</span>
      <span className="nx-attach-info">
        <span className="nx-attach-name">{attachment.filename}</span>
        {error ? (
          <span className="nx-attach-meta nx-attach-err">{error}</span>
        ) : (
          <span className="nx-attach-meta">{formatBytes(attachment.size_bytes)}</span>
        )}
      </span>
    </button>
  );
}

/* Consecutive messages from the same person within this window are grouped:
   tighter spacing, one timestamp. A back-and-forth otherwise renders as a
   stack of evenly spaced islands with a clock on every one. */
const GROUP_WINDOW_MS = 4 * 60 * 1000;

export default function Messages() {
  const { threadId } = useParams();
  // Only the view in front of the user mounts, so only it fetches and polls.
  return (
    <NetworkPage active="messages">
      {threadId ? <Conversation key={threadId} threadId={threadId} /> : <ThreadList />}
    </NetworkPage>
  );
}

/* ------------------------------------------------------------------ list */

function ThreadList() {
  const { token, isAdvocate, loading: authLoading } = useAuth();
  const navigate = useNavigate();

  const [threads, setThreads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(
    async ({ quiet } = {}) => {
      if (!quiet) setLoading(true);
      try {
        setThreads(await fetchThreads(token));
        setError('');
      } catch (err) {
        if (!quiet) setError(err.message);
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [token]
  );

  useEffect(() => {
    if (authLoading || !token) return undefined;
    load();
    const t = setInterval(() => load({ quiet: true }), THREAD_POLL_MS);
    return () => clearInterval(t);
  }, [authLoading, token, load]);

  const unreadTotal = threads.reduce((n, t) => n + (t.unread_count || 0), 0);

  return (
    <div className="page-scroll">
      <div className="page-wrap nx-narrow">
        <div className="page-head">
          <div>
            <h1>Messages</h1>
            <p className="page-sub">
              {isAdvocate
                ? 'Private conversations with the clients you have accepted.'
                : 'Private conversations with the advocates you are connected to.'}
              {unreadTotal > 0 && ` ${unreadTotal} unread.`}
            </p>
          </div>
        </div>

        {error && <div className="form-error">{error}</div>}

        {loading ? (
          <div className="nx-list">
            {[0, 1, 2].map((i) => (
              <div className="nx-thread-skel" key={i}>
                <span className="skel-ic" />
                <span className="skel-body">
                  <span className="skel-line w40" />
                  <span className="skel-line w80" />
                </span>
              </div>
            ))}
          </div>
        ) : threads.length === 0 ? (
          <Empty
            icon="inbox"
            title="No conversations yet"
            text={
              isAdvocate
                ? 'Accept a connection request and a private conversation opens automatically.'
                : 'Once an advocate accepts your request, your conversation appears here.'
            }
            action={isAdvocate ? null : { label: 'Find an Advocate', to: '/advocates' }}
            onAction={navigate}
          />
        ) : (
          <div className="nx-list">
            {threads.map((t) => (
              <button
                type="button"
                key={t.id}
                className={`nx-thread-card ${t.unread_count > 0 ? 'unread' : ''}`}
                onClick={() => navigate(`/messages/${t.id}`)}
              >
                <Avatar user={t.other} size={46} />
                <span className="nx-thread-main">
                  <span className="nx-thread-top">
                    <span className="nx-thread-name">
                      {t.other?.name || 'Conversation'}
                    </span>
                    <DemoBadge user={t.other} />
                    {t.other_specialization && (
                      <span className="nx-thread-spec">{t.other_specialization}</span>
                    )}
                  </span>
                  <span className="nx-thread-preview">
                    {t.last_message || 'No messages yet'}
                  </span>
                </span>
                <span className="nx-thread-side">
                  <span className="nx-thread-time">
                    {t.last_message_at ? timeAgo(t.last_message_at) : ''}
                  </span>
                  {t.unread_count > 0 && (
                    <span className="nx-thread-badge">{t.unread_count}</span>
                  )}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- conversation */

function Conversation({ threadId }) {
  const { token, isAdvocate } = useAuth();
  const navigate = useNavigate();

  const [meta, setMeta] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sendError, setSendError] = useState('');
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  // The uploaded-but-not-yet-sent file: { id, filename, content_type, size_bytes }.
  const [attachment, setAttachment] = useState(null);
  const [uploading, setUploading] = useState(false);

  const scrollRef = useRef(null);
  const textRef = useRef(null);
  const fileInputRef = useRef(null);
  const lastIdRef = useRef(0);
  // Whether the view was pinned to the bottom BEFORE this render. Jumping
  // someone to the newest message while they read back through the history
  // is worse than making them scroll down themselves.
  const wasAtBottomRef = useRef(true);

  const merge = useCallback((incoming) => {
    if (!incoming?.length) return;
    setMessages((prev) => {
      // Dedupe by id: a just-sent message is appended immediately AND comes
      // back in the next poll.
      const seen = new Set(prev.map((m) => m.id));
      const fresh = incoming.filter((m) => !seen.has(m.id));
      if (!fresh.length) return prev;
      return [...prev, ...fresh].sort((a, b) => a.id - b.id);
    });
    const top = Math.max(...incoming.map((m) => m.id));
    if (top > lastIdRef.current) lastIdRef.current = top;
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    lastIdRef.current = 0;
    setMessages([]);

    (async () => {
      try {
        const [m, rows] = await Promise.all([
          fetchThread(token, threadId),
          fetchMessages(token, threadId),
        ]);
        if (!alive) return;
        setMeta(m);
        setMessages(rows);
        lastIdRef.current = rows.length ? rows[rows.length - 1].id : 0;
        setError('');
        markThreadRead(token, threadId).then(refreshBadges).catch(() => {});
      } catch (err) {
        if (alive) setError(err.message);
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [threadId, token]);

  useEffect(() => {
    if (loading || error) return undefined;
    let alive = true;

    const tick = async () => {
      try {
        const rows = await fetchMessages(token, threadId, {
          after_id: lastIdRef.current || undefined,
        });
        if (!alive || !rows?.length) return;
        merge(rows);
        // Anything arriving from the other side while this thread is open
        // counts as read, so the badge doesn't tick up on a conversation
        // the user is staring at.
        if (rows.some((m) => !m.mine)) {
          markThreadRead(token, threadId).then(refreshBadges).catch(() => {});
        }
      } catch (_) {
        /* A dropped poll is not worth an error banner - the next one in
           three seconds picks up whatever was missed. */
      }
    };

    const t = setInterval(tick, MESSAGE_POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [loading, error, threadId, token, merge]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (wasAtBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    // 60px of slack: "close enough to the bottom" rather than exactly on it,
    // which is impossible to hold with a growing list.
    wasAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }

  function clearAttachment() {
    setAttachment(null);
    // Without resetting the input's value, picking the same file twice in a
    // row fires no change event and the upload silently no-ops.
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  async function handleFilePicked(e) {
    const file = e.target.files?.[0];
    if (!file) return;

    if (file.size > MAX_UPLOAD_BYTES) {
      setSendError('That file is larger than 10 MB. Try a smaller one.');
      clearAttachment();
      return;
    }

    setSendError('');
    setUploading(true);
    try {
      const doc = await uploadDocument(token, file);
      setAttachment({
        id: doc.id,
        filename: doc.filename,
        content_type: doc.content_type,
        size_bytes: doc.size_bytes,
      });
    } catch (err) {
      setSendError(err.message);
      clearAttachment();
    } finally {
      setUploading(false);
    }
  }

  async function send() {
    const content = draft.trim();
    if ((!content && !attachment) || sending || uploading) return;
    setSending(true);
    setSendError('');
    wasAtBottomRef.current = true;
    try {
      const msg = await postMessage(token, threadId, {
        content,
        document_id: attachment?.id,
      });
      merge([msg]);
      setDraft('');
      clearAttachment();
      if (textRef.current) textRef.current.style.height = 'auto';
    } catch (err) {
      // The draft is deliberately NOT cleared - a failed send that also
      // eats what you typed is the worst outcome here.
      setSendError(err.message);
    } finally {
      setSending(false);
      textRef.current?.focus();
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function onInput(e) {
    setDraft(e.target.value);
    // Grow with the content, up to a ceiling, so a long case description is
    // visible while typing instead of scrolling inside two lines.
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 176)}px`;
  }

  /* Where a suggested question lands. It goes into the composer rather than
     being sent, because the advocate should read and edit it first - an AI
     question fired straight at a client is exactly the thing this feature
     is not. Appends rather than replaces, so selecting three questions and
     then a fourth doesn't discard the first three. */
  const insertIntoDraft = useCallback((text) => {
    if (!text) return;
    setDraft((prev) => (prev.trim() ? `${prev.trimEnd()}\n\n${text}` : text));
    // Defer: the textarea has to hold the new value before its scrollHeight
    // means anything.
    requestAnimationFrame(() => {
      const el = textRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 176)}px`;
    });
  }, []);

  /* AI document intelligence. Advocate-only, and it owns all of its own
     state - this page hands it the thread and a way into the composer, and
     otherwise carries three lines of JSX for it. */
  const intel = useDocIntel({
    token,
    threadId,
    enabled: isAdvocate,
    onInsertText: insertIntoDraft,
  });

  /* Precomputed so the render stays declarative: which messages start a new
     day, and which start a new group from the same sender. */
  const rows = useMemo(
    () =>
      messages.map((m, i) => {
        const prev = messages[i - 1];
        const newDay = !prev || dayLabel(prev.created_at) !== dayLabel(m.created_at);
        const sameSender = !!prev && prev.sender_id === m.sender_id;
        // parseUtc, not new Date(): the API sends naive UTC with no Z, and
        // hand-rolling the suffix here would drift from the rest of the app.
        const gap = prev
          ? (parseUtc(m.created_at) ?? 0) - (parseUtc(prev.created_at) ?? 0)
          : Infinity;
        return {
          ...m,
          newDay,
          grouped: sameSender && !newDay && gap < GROUP_WINDOW_MS,
        };
      }),
    [messages]
  );

  const other = meta?.other;
  const spec = meta?.other_specialization;

  if (error) {
    return (
      <div className="page-scroll">
        <div className="page-wrap nx-narrow">
          <Empty
            icon="inbox"
            title="Conversation not available"
            text={error}
            action={{ label: 'Back to messages', to: '/messages' }}
            onAction={navigate}
          />
        </div>
      </div>
    );
  }

  return (
    /* A row: conversation, then the AI panel when one is open. The chat
       keeps its own scroll and keeps polling while the panel is up - that
       is the difference between a workspace and a modal. */
    <div className="di-shell">
      <div className="nx-chat">
        <header className="nx-chat-head">
          <div className="nx-chat-head-inner">
            <button
              type="button"
              className="nx-chat-back"
              onClick={() => navigate('/messages')}
              aria-label="Back to conversations"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor"
                   strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11.5 5 6.5 10l5 5" />
              </svg>
            </button>
            {!other ? (
              /* Until the thread loads there is nothing true to say about who
                 is on the other end. This used to fall back to "Conversation"
                 and "Member" with a "U" avatar, which named a real person
                 wrongly for a moment on every open. */
              <div className="nx-chat-head-skel">
                <span className="skel-ic" />
                <span className="skel-body">
                  <span className="skel-line w40" />
                  <span className="skel-line w60" />
                </span>
              </div>
            ) : (
              <>
                <Avatar user={other} size={40} />
                <div className="nx-chat-ident">
                  <div className="nx-chat-name">
                    <strong>{other.name}</strong>
                    <DemoBadge user={other} />
                  </div>
                  <div className="nx-chat-role">
                    {other.role === 'advocate' ? 'Advocate' : 'Member'}
                    {spec && (
                      <>
                        <span className="nx-dot">&middot;</span>
                        {spec}
                      </>
                    )}
                  </div>
                </div>
                {other.role === 'advocate' && (
                  <Link className="btn btn-ghost sm" to={`/advocates/${other.id}`}>
                    Profile
                  </Link>
                )}
              </>
            )}
          </div>
        </header>

        <div className="nx-chat-scroll" ref={scrollRef} onScroll={onScroll}>
          <div className="nx-chat-stream">
            {loading ? (
              <div className="nx-chat-loading">Loading conversation&hellip;</div>
            ) : rows.length === 0 ? (
              <div className="nx-chat-intro">
                <h4>You&rsquo;re connected</h4>
                <p>
                  Explain what happened, when it started, who is involved and
                  where. Mention whether an FIR, notice or case has already been
                  filed, and what help you are looking for.
                </p>
              </div>
            ) : (
              rows.map((m) => (
                <div key={m.id}>
                  {m.newDay && (
                    <div className="nx-chat-day">
                      <span>{dayLabel(m.created_at)}</span>
                    </div>
                  )}
                  <div
                    className={`nx-msg ${m.mine ? 'mine' : ''} ${
                      m.grouped ? 'grouped' : ''
                    }`}
                  >
                    <div className="nx-msg-bubble">
                      {m.attachment && (
                        <div className="di-attach-row">
                          <AttachmentCard attachment={m.attachment} token={token} />
                          {intel.menuFor(m.attachment)}
                        </div>
                      )}
                      {m.content && <span className="nx-msg-text">{m.content}</span>}
                      <span className="nx-msg-time">
                        {clockTime(m.created_at)}
                        {m.mine && m.read_at && (
                          <span className="nx-msg-read" title="Read">
                            &#10003;&#10003;
                          </span>
                        )}
                      </span>
                    </div>
                  </div>
                  {/* The analysis belongs to THIS message, so it renders
                      under it in the stream rather than being sent into the
                      conversation as a message of its own - which would put
                      the advocate's working notes in front of the client. */}
                  {m.attachment && intel.cardFor(m.attachment)}
                </div>
              ))
            )}
          </div>
        </div>

        {/* The SAME composer as Ask / Draft / Review / Arguments - literally the
            same classes from styles.css (.ask-form-dock > .ask-dock-row >
            .dock-box > .dock-input + .dock-send), not a copy of their values.
            Reusing them is what guarantees the box stays identical everywhere
            instead of drifting the next time either side is touched. Only the
            placeholder differs by context. */}
        <form
          className="ask-form-dock nx-dock"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <div className="ask-dock-row">
            {sendError && <div className="form-error nx-dock-error">{sendError}</div>}
            {attachment && (
              <div className="attach-chip">
                <span className="attach-chip-ic">{FileIcon}</span>
                <span className="attach-chip-name">{attachment.filename}</span>
                <button
                  type="button"
                  className="attach-chip-x"
                  onClick={clearAttachment}
                  aria-label="Remove attachment"
                >
                  ×
                </button>
              </div>
            )}
            <div className="dock-box">
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPT}
                onChange={handleFilePicked}
                style={{ display: 'none' }}
              />
              <button
                type="button"
                className="attach-btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading || sending || !!attachment}
                title="Attach a document"
                aria-label="Attach a document"
              >
                {uploading ? <span className="spinner spinner-dark" /> : ClipIcon}
              </button>
              <textarea
                ref={textRef}
                className="dock-input"
                rows={1}
                maxLength={MAX_CHARS}
                value={draft}
                onChange={onInput}
                onKeyDown={onKeyDown}
                placeholder={attachment ? 'Add a caption\u2026 (optional)' : 'Type your message\u2026'}
                aria-label="Message"
              />
              {/* Only surfaces when the limit is actually in reach. */}
              {draft.length > MAX_CHARS - 500 && (
                <span className="nx-dock-count">
                  {draft.length}/{MAX_CHARS}
                </span>
              )}
              <button
                className="dock-send"
                type="submit"
                disabled={sending || uploading || (!draft.trim() && !attachment)}
                aria-label="Send message"
              >
                {sending ? <span className="spinner" /> : 'Send'}
              </button>
            </div>
          </div>
        </form>
      </div>

      {intel.panel}
    </div>
  );
}