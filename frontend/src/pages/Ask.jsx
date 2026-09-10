import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth, ACTIVE_CONVERSATION_KEY } from '../AuthContext';
import ThemeToggle from '../components/ThemeToggle';
import ArgumentsResult from '../components/ArgumentsResult';
import ArgumentsSetup from '../components/ArgumentsSetup';
import DocTypeSelect from '../components/DocTypeSelect';
import {
  askQuestion,
  askQuestionStream,
  translateAnswer,
  translateText,
  createDraft,
  deleteConversation,
  downloadDraftDocx,
  fetchConversation,
  fetchConversations,
  fetchArgumentSides,
  fetchDraftTypes,
  generateArguments,
  reviewDocument,
  uploadDocument,
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
  argue: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 3v14M5.5 17h9" />
      <path d="M2.5 7.5l3-3.5 3 3.5a3 3 0 0 1-6 0z" />
      <path d="M11.5 7.5l3-3.5 3 3.5a3 3 0 0 1-6 0z" />
    </svg>
  ),
  matters: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round">
      <path d="M2.5 6.5a1.5 1.5 0 0 1 1.5-1.5h3l1.5 2h6a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5z" />
    </svg>
  ),
};

// Mirrors storage.ALLOWED_TYPES on the backend. Listing them here only
// filters the OS picker - the server still rejects anything else.
const ACCEPT =
  '.pdf,.doc,.docx,.txt,.jpg,.jpeg,.png,.webp,' +
  'application/pdf,application/msword,text/plain,' +
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document,' +
  'image/jpeg,image/png,image/webp';

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

const ALL_MODES = [
  { id: 'ask', label: 'Ask', icon: Icon.ask },
  // Draft and Review are open to every account - see /draft and /review on
  // the backend. Arguments stays advocate-only: it builds one-sided
  // advocacy for a case rather than neutral legal information.
  { id: 'draft', label: 'Draft', icon: Icon.draft },
  { id: 'review', label: 'Review', icon: Icon.review },
  { id: 'argue', label: 'Arguments', icon: Icon.argue, advocateOnly: true },
  // Not a mode - it navigates away to the matter workspace.
  { id: 'matters', label: 'Matters', icon: Icon.matters, advocateOnly: true, route: '/matters' },
];

const PLACEHOLDERS = {
  ask: 'Ask a legal question, e.g. "can my landlord evict me without notice?"',
  draft: 'Describe what you need, e.g. "Rakesh gave me a cheque for 85,000 that bounced on 12 August 2026"',
  review: "Paste the contract or notice you were sent, or upload it, and I'll flag the risky clauses.",
  argue: 'Set out the facts of the matter — what happened, who did what, what is being claimed.',
};

// Advocates get the research framing; everyone else gets plain language.
const WELCOME_SUB_ADVOCATE = 'Research, draft, and review — with sources you can check.';

const WELCOME_SUB = {
  ask: 'Ask any legal question, in plain language.',
  draft: 'Tell me what document you need, and I’ll draft it.',
  review: 'Paste a contract or notice and I’ll flag the risky parts.',
  argue: 'Build your case — arguments, the case against, and how to answer it.',
};

// General users are here to understand the law; advocates are here to work.
// Same engine, different doorways in.
const USER_CHIPS = [
  { label: 'What is the BNS?', fill: 'What is the Bharatiya Nyaya Sanhita and how is it different from the IPC?' },
  { label: 'Fundamental Rights', fill: 'What are my fundamental rights under the Constitution of India?' },
  { label: 'Filing an FIR', fill: 'How do I file an FIR, and what can I do if the police refuse to register one?' },
  { label: 'Cheque Bounced', fill: 'My cheque bounced — what should I do now?' },
  { label: 'Landlord & Rent', fill: 'Can my landlord increase my rent or evict me without notice?' },
  { label: 'Consumer Complaint', fill: 'How do I file a complaint in the consumer forum?' },
  { label: 'Right to Information', fill: 'How do I file an RTI application?' },
  { label: 'Arrest & Bail Basics', fill: 'What are my rights if I am arrested, and how does bail work?' },
];

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
  argue: [],
};

// Owned by AuthContext, which has to clear it on login and logout. Aliased
// here so the two files can never disagree about the key name.
const STORAGE_KEY = ACTIVE_CONVERSATION_KEY;

/* Which thread this tab was last reading. sessionStorage, so it dies with the
   tab, and every access is guarded - storage throws outright in some private
   modes, and losing a convenience is no reason to take the page down. */
const thread = {
  get() {
    try { return sessionStorage.getItem(STORAGE_KEY); } catch (_) { return null; }
  },
  remember(id) {
    try { sessionStorage.setItem(STORAGE_KEY, String(id)); } catch (_) { /* ignore */ }
  },
  forget() {
    try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) { /* ignore */ }
  },
};

/* Was this document actually reloaded - F5, or a browser restoring the tab?
   Anything else (following a link, logging in, a router transition) is an
   arrival, and an arrival should start a new question. */
function wasPageReload() {
  try {
    const [nav] = performance.getEntriesByType('navigation');
    if (nav) return nav.type === 'reload';
    // Safari and older browsers predate Navigation Timing Level 2.
    return performance.navigation?.type === 1;
  } catch (_) {
    return false;
  }
}

/* Module scope, evaluated once per document load, and consumed by the first
   mount that reads it.
   
   It has to live outside the component. Ask mounts again every time you come
   back from Matters or the auth guard finishes, and a reload flag that reset
   with the component would make each of those look like a reload - which is
   the bug: you land on Ask, and the thread you were reading last week opens
   itself, so the next question you ask goes into it. */
let restoreAllowed = wasPageReload();

const MODES_SET = ['ask', 'draft', 'review', 'argue'];

function urlMode(params) {
  const m = params.get('mode');
  return MODES_SET.includes(m) ? m : 'ask';
}

// Ask, Draft, and Review threads are all saved now. Arguments still isn't -
// /arguments never writes a Conversation/QueryLog, unlike the other three -
// so it keeps its own honest empty state.
const MODE_LABEL = {
  ask: 'Recents',
  draft: 'Drafts',
  review: 'Reviews',
  argue: 'Argument sets',
};

const MODE_EMPTY = {
  ask: 'Nothing asked yet — try a question on the right.',
  draft: 'Nothing drafted yet — describe what you need on the right.',
  review: 'Nothing reviewed yet — paste or attach a document on the right.',
  argue: 'Argument sets are not saved yet — use Copy all before you leave.',
};

// The composer starts one line tall and grows with the text, like every other
// chat box people already know. Capped so a pasted page of facts can't push
// the conversation off screen.
const DOCK_MAX_H = 180;

function autoGrow(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, DOCK_MAX_H)}px`;
}

export default function Ask() {
  const { token, user, logout, isAdvocate } = useAuth();
  const navigate = useNavigate();
  // /ask?mode=draft lets the Matters page link back into a specific tool.
  const [searchParams, setSearchParams] = useSearchParams();
  const [mode, setMode] = useState(() => urlMode(searchParams));
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
  // The uploaded file, once the server has it: { id, filename }.
  const [attachment, setAttachment] = useState(null);
  const [uploading, setUploading] = useState(false);
  // Generate Arguments setup: side is required, the rest sharpen retrieval.
  const [sides, setSides] = useState([]);
  const [side, setSide] = useState('petitioner');
  const [issue, setIssue] = useState('');
  const [court, setCourt] = useState('');
  const [setupOpen, setSetupOpen] = useState(false);
  const threadEndRef = useRef(null);
  const fileInputRef = useRef(null);
  // True once the user has done anything that owns the screen - sent a
  // question, attached a file, switched tools, started a new thread, or
  // opened a thread from the sidebar. The restore below is a convenience and
  // must never paint over any of those, however late its fetch resolves.
  const userActedRef = useRef(false);

  useEffect(() => {
    if (!token) return;
    refreshHistory();

    // Reopening the last thread is for one case only: the page was reloaded
    // mid-conversation and the screen would otherwise go blank. Consumed
    // here, so later mounts in the same document - returning from Matters,
    // switching tools, the auth guard resolving - are arrivals and start a
    // new question, whichever surface and whichever role.
    if (!restoreAllowed) return;
    restoreAllowed = false;

    // A URL that names its own mode is someone arriving from Matters to open
    // Draft or Review. Reopening an older thread would drag them straight
    // back out of the tool they picked.
    if (searchParams.get('mode')) return;

    const saved = thread.get();
    if (saved) loadConversation(Number(saved), { restore: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Draft types and argument sides are static lists that most sessions never
  // need. Fetching them on every Ask page load added two requests before the
  // page could settle; now they load the first time you open the tool that
  // uses them.
  useEffect(() => {
    if (!token) return;
    // Draft and Review types are available to every account.
    if ((mode === 'draft' || mode === 'review') && types.length === 0) {
      fetchDraftTypes(token).then(setTypes).catch(() => {});
    }
    // Arguments stays advocate-only.
    if (mode === 'argue' && isAdvocate && sides.length === 0) {
      fetchArgumentSides(token).then(setSides).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, isAdvocate, mode]);

  // Safety net: a general user restoring a saved Arguments thread, or an
  // account whose role changed, snaps back to Ask instead of a dead screen.
  // Draft and Review need no such guard - every account can use them.
  useEffect(() => {
    if (!isAdvocate && mode === 'argue') switchMode('ask');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdvocate, mode]);

  // /ask?mode=draft and /ask?mode=ask are the SAME route, so React Router
  // doesn't remount when only the query string changes - the useState
  // initialiser runs once and never again. Without this, arriving from
  // Matters (or the back button) changed the address bar and nothing else,
  // leaving the nav highlight and the visible answer out of step.
  useEffect(() => {
    const next = urlMode(searchParams);
    if (next !== mode) switchMode(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns, loading]);

  useEffect(() => {
    localStorage.setItem('ns_sidebar', sidebarOpen ? 'open' : 'closed');
  }, [sidebarOpen]);

  function refreshHistory({ fresh = false } = {}) {
    fetchConversations(token, { fresh }).then(setHistory).catch(() => {});
  }

  async function loadConversation(id, { restore = false } = {}) {
    // A restore that resolves after the user has already moved on must be
    // dropped, not applied. This was the bug that pulled people into their
    // previous thread the moment they asked something after logging in: the
    // question went out, the restore landed a beat later, and its setTurns
    // and setConversationId overwrote the question that was in flight.
    //
    // Checked twice on purpose - once before the fetch, once after - because
    // the user can act during the round trip.
    if (restore && userActedRef.current) return;
    if (!restore) userActedRef.current = true;   // opening a thread is an act

    try {
      const data = await fetchConversation(token, id);
      if (restore && userActedRef.current) return;

      setConversationId(data.id);
      setModeAndUrl(MODES_SET.includes(data.mode) ? data.mode : 'ask');
      setTurns(
        data.turns.map((t) => ({
          kind: t.mode || 'ask',
          prompt: t.question,
          ...(t.payload || { title: t.answer_title, body: t.answer_body, citations: [], next_steps: [] }),
          // The stored payload predates translation and has no query_log_id
          // of its own, so it comes from the turn row. Spread last, or an
          // older payload would leave a reloaded answer untranslatable.
          query_log_id: t.id,
        }))
      );
      thread.remember(data.id);
      setError('');
    } catch {
      thread.forget();   // thread was deleted
    }
  }

  const started = loading || turns.length > 0 || !!error;
  const modes = isAdvocate ? ALL_MODES : ALL_MODES.filter((m) => !m.advocateOnly);
  // The sidebar list scoped to whichever surface is open - a draft thread
  // has no business appearing under "Recents" while you're asking a
  // question, and vice versa.
  const visibleHistory = history.filter((h) => h.mode === mode);
  // Draft writes a document from a description; there is nothing to read in.
  const canAttach = mode === 'ask' || mode === 'review' || mode === 'argue';
  // With a file attached, Review needs no typing and Ask needs only a nudge.
  const canSubmit = !loading && !uploading && (!!input.trim() || (!!attachment && canAttach));
  const chips = mode === 'ask' && !isAdvocate ? USER_CHIPS : CHIPS[mode];
  const welcomeSub =
    mode === 'ask' && isAdvocate ? WELCOME_SUB_ADVOCATE : WELCOME_SUB[mode];

  function clearThread() {
    setTurns([]);
    setConversationId(null);
    setError('');
    clearAttachment();
    thread.forget();
  }

  // Keeps `mode` and the ?mode= parameter in step. Everything that changes
  // the surface goes through here, so the nav highlight, the composer and the
  // visible answer can never disagree about which tool you are in.
  function setModeAndUrl(next) {
    setMode(next);
    if (urlMode(searchParams) !== next) {
      setSearchParams(next === 'ask' ? {} : { mode: next }, { replace: true });
    }
  }

  function switchMode(next) {
    if (next === mode) return;   // a no-op click shouldn't wipe the thread
    userActedRef.current = true;
    setModeAndUrl(next);
    clearThread();
    // Arguments needs a side before it can do anything, so ask up front
    // rather than letting the advocate type a page of facts and then find out.
    setSetupOpen(next === 'argue');
  }

  function clearAttachment() {
    setAttachment(null);
    // Resetting the input's value matters: without it, picking the same file
    // twice in a row fires no change event and the upload silently no-ops.
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  async function handleFilePicked(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    userActedRef.current = true;

    if (file.size > MAX_UPLOAD_BYTES) {
      setError('That file is larger than 10 MB. Try a smaller one.');
      clearAttachment();
      return;
    }

    setError('');
    setUploading(true);
    try {
      const doc = await uploadDocument(token, file);
      setAttachment({ id: doc.id, filename: doc.filename });
    } catch (err) {
      setError(err.message);
      clearAttachment();
    } finally {
      setUploading(false);
    }
  }

  function startNew() {
    // Not switchMode: that returns early when the mode is unchanged, which
    // would make "New question" do nothing while already on Ask.
    userActedRef.current = true;
    setModeAndUrl('ask');
    setSetupOpen(false);
    clearThread();
    setInput('');
    setDocType('');
  }

  async function removeConversation(e, id) {
    e.stopPropagation();
    try {
      await deleteConversation(token, id);
      if (id === conversationId) startNew();
      refreshHistory({ fresh: true });
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
    if (!canSubmit) return;
    userActedRef.current = true;

    const prompt = input;
    const sentFile = canAttach ? attachment : null;
    // What the user sees echoed back. A bare filename is a clearer record of
    // "I asked about this file" than an empty bubble.
    const bubble = prompt.trim() || (sentFile ? sentFile.filename : '');

    setError('');
    setLoading(true);
    setInput('');
    setTurns((t) => [...t, { kind: 'pending', prompt: bubble, file: sentFile?.filename }]);

    try {
      let turn;
      if (mode === 'ask') {
        // The pending turn becomes the answer in place: text lands in it as
        // it streams, then the finished payload replaces it wholesale. The
        // reader never sees the card jump.
        //
        // Always the last turn - submit is gated on `loading`, so nothing
        // else can be appended while this one is streaming. Indexing from the
        // end avoids holding a stale position across re-renders.
        const patchLast = (fn) =>
          setTurns((t) => {
            if (!t.length) return t;
            const next = [...t];
            next[next.length - 1] = fn(next[next.length - 1]);
            return next;
          });

        patchLast((turn0) => ({ ...turn0, kind: 'streaming', body: '' }));

        const append = (text) =>
          patchLast((turn0) => ({ ...turn0, body: (turn0.body || '') + text }));

        const replace = (body) =>
          patchLast((turn0) => ({ ...turn0, body, revised: true }));

        const data = await askQuestionStream(
          token,
          {
            question: prompt,
            state: user?.state,
            conversation_id: conversationId,
            document_id: sentFile?.id,
          },
          { onDelta: append, onRevised: replace },
        );

        if (data.conversation_id) {
          setConversationId(data.conversation_id);
          thread.remember(data.conversation_id);
        }
        turn = { kind: 'ask', ...data, prompt: bubble, file: sentFile?.filename };
        refreshHistory({ fresh: true });   // the new thread must appear now
      } else if (mode === 'draft') {
        const data = await createDraft(token, {
          doc_type: docType || null,
          instructions: prompt,
          details: null,
          conversation_id: conversationId,
        });
        if (data.conversation_id) {
          setConversationId(data.conversation_id);
          thread.remember(data.conversation_id);
        }
        turn = { kind: 'draft', ...data, prompt: bubble };
        refreshHistory({ fresh: true });   // the new thread must appear now
      } else if (mode === 'review') {
        const data = await reviewDocument(token, {
          // A pasted clause and an uploaded file are both valid; the backend
          // prefers the text when both arrive.
          document_text: prompt.trim() || null,
          document_id: sentFile?.id,
          doc_type: docType || null,
          context: null,
          conversation_id: conversationId,
        });
        if (data.conversation_id) {
          setConversationId(data.conversation_id);
          thread.remember(data.conversation_id);
        }
        turn = { kind: 'review', ...data, prompt: bubble, file: sentFile?.filename };
        refreshHistory({ fresh: true });
      } else if (mode === 'argue') {
        const data = await generateArguments(token, {
          facts: prompt.trim() || null,
          document_id: sentFile?.id,
          side,
          issue: issue.trim() || null,
          court: court.trim() || null,
          state: user?.state,
        });
        turn = { kind: 'argue', ...data, prompt: bubble, file: sentFile?.filename };
      } else {
        // Unreachable - every mode is handled above. Kept so a future mode
        // fails loudly here rather than silently rendering nothing.
        throw new Error('Unknown mode.');
      }
      setTurns((t) => [...t.slice(0, -1), turn]);
      clearAttachment();
    } catch (err) {
      if (err.terminal) {
        // Refused, not dropped - an out-of-scope document, or input that
        // wasn't a request. The pending turn becomes the refusal in place, so
        // the thread reads as question-then-reply and the user can see what
        // was rejected and why. The composer clears: resending the same thing
        // would only be refused again, and leaving it in the box invites
        // exactly that.
        setTurns((t) => [
          ...t.slice(0, -1),
          {
            kind: 'rejected',
            prompt: bubble,
            file: sentFile?.filename,
            message: err.message,
          },
        ]);
        clearAttachment();
      } else {
        // A blip - a dropped connection, a timeout. Roll the turn back and
        // hand the question and the upload back so the same send can be
        // retried without retyping or re-attaching.
        setTurns((t) => t.slice(0, -1));
        setError(err.message);
        setInput(prompt);
      }
    } finally {
      setLoading(false);
    }
  }

  // Arguments is a structured object, so flatten it to something an advocate
  // can paste into a brief rather than copying JSON.
  // Flattens the structured argument set into something an advocate can paste
  // into a brief. Shared by Copy all and Download so the two can't drift.
  function argumentsAsText(data) {
    const lines = [data.title, `For the ${data.side}`, ''];
    const push = (heading, items, fmt) => {
      if (!items?.length) return;
      lines.push(heading.toUpperCase(), '');
      items.forEach((x, i) => { lines.push(fmt(x, i)); lines.push(''); });
    };

    push('Issues', data.issues, (x, i) => `${i + 1}. ${x.question}`);
    push('Arguments', data.arguments, (x, i) =>
      `${i + 1}. ${x.title} [${x.strength}]\n${x.proposition}\n${x.legal_basis}\n${x.application}`);
    push('Alternative arguments', data.alternative_arguments, (x, i) =>
      `${i + 1}. ${x.title}\n${x.proposition}`);
    push('Opposing arguments', data.opposing_arguments, (x, i) =>
      `${i + 1}. ${x.title} [${x.strength}]\n${x.position}`);
    push('Rebuttals', data.rebuttals, (x, i) =>
      `${i + 1}. ${x.opposing_argument}\n${x.response}`);
    push('Weaknesses', data.weaknesses, (x, i) =>
      `${i + 1}. ${x.issue}\nRisk: ${x.risk}\nMitigation: ${x.mitigation}`);
    push('Strategy', data.strategy, (x, i) => `${i + 1}. ${x}`);
    push('Sources', data.sources, (x, i) =>
      `${i + 1}. ${x.title} — ${x.source}${x.url ? ` ${x.url}` : ''}`);

    lines.push('---',
      'Prepared with Nyaya Sathi. Research assistance, not settled opinion —',
      'open and read every authority before you cite it.');
    return lines.join('\n');
  }

  function copyArguments(data, idx) {
    navigator.clipboard.writeText(argumentsAsText(data)).then(() => {
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx(null), 2000);
    });
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

  const fileInput = (
    <input
      ref={fileInputRef}
      type="file"
      accept={ACCEPT}
      onChange={handleFilePicked}
      style={{ display: 'none' }}
    />
  );

  const attachButton = canAttach && (
    <button
      type="button"
      className="attach-btn"
      onClick={() => fileInputRef.current?.click()}
      disabled={uploading || loading}
      title={mode === 'review' ? 'Upload a document to review' : 'Attach a document'}
      aria-label="Attach a document"
    >
      {uploading ? <span className="spinner spinner-dark" /> : Icon.clip}
    </button>
  );

  const attachChip = attachment && canAttach && (
    <div className="attach-chip">
      <span className="attach-chip-ic">{Icon.file}</span>
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
  );

  const argueSetup = mode === 'argue' && setupOpen && (
    <ArgumentsSetup
      sides={sides.length ? sides : [{ id: 'petitioner', label: 'Petitioner' }]}
      side={side}
      onSideChange={setSide}
      issue={issue}
      onIssueChange={setIssue}
      court={court}
      onCourtChange={setCourt}
      attachment={attachment}
      uploading={uploading}
      onPickFile={() => fileInputRef.current?.click()}
      onClearFile={clearAttachment}
      onClose={() => setSetupOpen(false)}
    />
  );

  // Once the modal is dismissed, the choices stay visible and editable -
  // realising you picked the wrong side after generating is expensive.
  const argueBar = mode === 'argue' && !setupOpen && (
    <div className="arg-bar">
      <span className="arg-bar-side">
        For the {(sides.find((x) => x.id === side) || {}).label || side}
      </span>
      {court && <span className="arg-bar-meta">{court}</span>}
      {issue && <span className="arg-bar-meta arg-bar-issue">{issue}</span>}
      <button type="button" className="link-btn" onClick={() => setSetupOpen(true)}>
        Change
      </button>
    </div>
  );

  // Sits inline in the composer's action row rather than above the box.
  // Opens upward (see .dock-box .dts-menu in styles.css) since the composer
  // sits at the bottom of the screen - opening downward, the default meant
  // to stop a native select covering the answer, would push the menu off
  // the bottom of the viewport here instead.
  const docSelect = mode !== 'ask' && mode !== 'argue' && (
    <DocTypeSelect
      value={docType}
      onChange={setDocType}
      groups={grouped}
      placeholder={mode === 'draft' ? 'Let Nyaya Sathi decide' : 'Document type'}
      disabled={loading}
    />
  );

  return (
    <div className="ask-app">
      {argueSetup}
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
          <ThemeToggle />
          <span className="user-chip">
            <span className="avatar">{user?.name?.[0]?.toUpperCase() || 'U'}</span>
            {user?.name?.split(' ')[0]}
            <span className={`role-badge ${isAdvocate ? 'advocate' : ''}`}>
              {isAdvocate ? 'Advocate' : 'Member'}
            </span>
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
            {modes.map((m) => (
              <button
                key={m.id}
                type="button"
                role="tab"
                aria-selected={mode === m.id}
                className={`ask-nav-item ${mode === m.id ? 'active' : ''}`}
                onClick={() => (m.route ? navigate(m.route) : switchMode(m.id))}
              >
                <span className="ask-nav-icon">{m.icon}</span>
                {m.label}
              </button>
            ))}
          </nav>

          <div className="ask-sidebar-divider" />

          <h4>{MODE_LABEL[mode]}</h4>
          <div className="ask-history-list">
            {/* Arguments has no saved history at all - always the empty
                state. Ask/Draft/Review share one list, filtered to the
                surface you're on, so switching tools doesn't show another
                tool's threads under this one's heading. */}
            {mode === 'argue' && (
              <div className="history-empty">{MODE_EMPTY.argue}</div>
            )}
            {mode !== 'argue' && visibleHistory.length === 0 && (
              <div className="history-empty">{MODE_EMPTY[mode]}</div>
            )}
            {mode !== 'argue' && visibleHistory.map((h) => (
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
                <div className="sub">
                  {isAdvocate ? 'Advocate' : 'Member'}
                  {user.state ? ` · ${user.state}` : ''}
                </div>
              </div>
            </div>
          )}
        </aside>

        <div className="ask-main">
          {!started ? (
            <div className="ask-welcome">
              <h1>Welcome to Nyaya Sathi</h1>
              <p className="ask-welcome-sub">{welcomeSub}</p>

              <form className="ask-input-card" onSubmit={handleSubmit}>
                {fileInput}
                {argueBar}
                {attachChip}
                {mode === 'review' || mode === 'argue' ? (
                  <textarea
                    className="ask-input-textarea"
                    rows={2}
                    ref={autoGrow}
                    value={input}
                    onChange={(e) => { setInput(e.target.value); autoGrow(e.target); }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        handleSubmit(e);
                      }
                    }}
                    placeholder={PLACEHOLDERS[mode]}
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
                  {attachButton}
                  {docSelect}
                  <span className="actions-spacer" />
                  <button type="submit" className="send-btn" disabled={!canSubmit}>
                    {loading ? <span className="spinner" /> : '\u2191'}
                  </button>
                </div>
              </form>

              {chips?.length > 0 && (
                <div className="ask-chip-grid">
                  {chips.map((c) => (
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
              <div className={`ask-thread ${mode === 'argue' ? 'thread-wide' : ''}`}>
                {turns.map((t, i) => (
                  <div className="ask-turn" key={i}>
                    <div className="ask-user-bubble">
                      {t.file && (
                        <span className="bubble-file">
                          {Icon.file} {t.file}
                        </span>
                      )}
                      {t.prompt !== t.file && t.prompt}
                    </div>
                    {t.kind === 'pending' ? (
                      <div className="ask-thinking">
                        <span className="spinner" /> Thinking through this…
                      </div>
                    ) : t.kind === 'streaming' ? (
                      <StreamingAnswer body={t.body} />
                    ) : t.kind === 'rejected' ? (
                      <div className="ask-rejection">{t.message}</div>
                    ) : (
                      <>
                        {t.kind === 'ask' && <AskResult data={t} token={token} />}
                        {t.kind === 'draft' && (
                          <DraftResult
                            data={t}
                            token={token}
                            // The visible text, not the English original - a
                            // Tamil draft copied as English is a bug.
                            onCopy={(text) => copyDraft(text ?? t.body, i)}
                            copied={copiedIdx === i}
                          />
                        )}
                        {t.kind === 'review' && <ReviewResult data={t} />}
                        {t.kind === 'argue' && (
                          <ArgumentsResult
                            data={t}
                            onCopy={() => copyArguments(t, i)}
                            copied={copiedIdx === i}
                          />
                        )}
                      </>
                    )}
                  </div>
                ))}

                {error && <div className="form-error">{error}</div>}
                <div ref={threadEndRef} />
              </div>

              <form
                className={`ask-form-dock ${mode === 'argue' ? 'dock-wide' : ''}`}
                onSubmit={handleSubmit}
              >
                {fileInput}
                <div className="ask-dock-row">
                  {argueBar}
                  {attachChip}
                  <div className="dock-box">
                    {attachButton}
                    {docSelect}
                    <textarea
                      className="dock-input"
                      rows={1}
                      value={input}
                      ref={autoGrow}
                      onChange={(e) => { setInput(e.target.value); autoGrow(e.target); }}
                      onKeyDown={(e) => {
                        // Enter sends, Shift+Enter breaks the line.
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          handleSubmit(e);
                        }
                      }}
                      placeholder={PLACEHOLDERS[mode]}
                    />
                    <button className="dock-send" type="submit" disabled={!canSubmit}>
                      {loading ? <span className="spinner" /> : '\u2191'}
                    </button>
                  </div>
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
            {href && <span className="cite-arrow" aria-hidden="true">&#8599;</span>}
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

/* How well the answer is supported. The backend derives this from what
   retrieval returned and what the prose cites — it is never a number the
   model gave itself, which is why it can be trusted enough to show. */
function Grounding({ data }) {
  if (!data) return <div className="status-chip live"><span className="dot" /> Answered live</div>;

  const title = data.reasons?.length ? data.reasons.join(' ') : undefined;
  return (
    <div className={`ground-chip g-${data.level}`} title={title}>
      <span className="dot" />
      {data.label}
      {data.repealed > 0 && <span className="ground-warn">repealed law cited</span>}
    </div>
  );
}

/* The answer mid-flight. Deliberately plain: citations, next steps and the
   grounding badge are genuinely not known until the answer is finished and
   validated, so showing placeholders for them would be a lie. */
function StreamingAnswer({ body }) {
  return (
    <div className="demo-card">
      <div className="demo-topbar">
        <div className="demo-brand"><span className="sq">न्या</span> Research</div>
        <span className="ground-pill"><span className="spinner" /> Writing…</span>
      </div>
      {(body || '').split(/\n{2,}/).map((p, i) => (
        <p className="answer-body" key={i}>{p}</p>
      ))}
    </div>
  );
}

/* The languages the answer can be rendered in. The interface itself stays in
   English throughout - this switches the generated prose only, which is the
   part the reader actually needs in their own language. Act names and section
   numbers stay in English inside every one of them: they are identifiers a
   person types into a search box or hands to a court clerk. */
const LANGUAGES = [
  { code: 'en', native: 'English' },
  { code: 'hi', native: 'हिन्दी' },
  { code: 'mr', native: 'मराठी' },
  { code: 'ta', native: 'தமிழ்' },
  { code: 'te', native: 'తెలుగు' },
  { code: 'kn', native: 'ಕನ್ನಡ' },
];

/* Switches one answer between languages. Each rendering is kept once fetched,
   so going back to a language already seen is instant - no second call, no
   spinner. The English text is never discarded: it is what the validator
   passed and what the grounding badge was computed against. */
function LanguageBar({ active, busy, onPick }) {
  return (
    <div className="lang-bar">
      {LANGUAGES.map((l) => (
        <button
          type="button"
          key={l.code}
          className={`lang-pill${l.code === active ? ' is-active' : ''}`}
          disabled={busy}
          onClick={() => onPick(l.code)}
        >
          {l.native}
        </button>
      ))}
      {busy && <span className="lang-busy"><span className="spinner" /> translating…</span>}
    </div>
  );
}

function AskResult({ data, token }) {
  const [lang, setLang] = useState('en');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState('');
  // Renderings already fetched, so switching back costs nothing. Seeded with
  // the English original, which is never refetched or overwritten.
  const [versions, setVersions] = useState({
    en: { title: data.title, body: data.body, next_steps: data.next_steps || [] },
  });

  const turnId = data.query_log_id;
  const shown = versions[lang] || versions.en;

  async function pick(code) {
    setFailed('');
    if (code === lang || busy) return;
    if (versions[code]) { setLang(code); return; }   // already have it
    if (!turnId) { setFailed("This answer can't be translated."); return; }

    setBusy(true);
    try {
      const r = await translateAnswer(token, { query_log_id: turnId, language: code });
      setVersions((v) => ({
        ...v,
        [code]: { title: r.title, body: r.body, next_steps: r.next_steps || [] },
      }));
      setLang(code);
    } catch {
      // The English answer stays on screen - a failed translation should
      // never leave the reader with nothing.
      setFailed('That translation could not be produced. Showing English.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="demo-card">
      <div className="demo-topbar">
        <div className="demo-brand"><span className="sq">न्या</span> Research</div>
        <Grounding data={data.grounding} />
      </div>
      <h4 className="answer-title">{shown.title}</h4>
      {(shown.body || '').split(/\n{2,}/).map((p, i) => (
        <p className="answer-body" key={i}>{p}</p>
      ))}

      <LanguageBar active={lang} busy={busy} onPick={pick} />
      {failed && <p className="lang-error">{failed}</p>}

      {/* Sources stay in English in every language: these are the actual
          titles of the judgments and sections, and a translated case name
          cannot be looked up. */}
      <Citations items={data.citations} />
      {shown.next_steps?.length > 0 && (
        <div className="demo-steps">
          <div className="label">Suggested next steps</div>
          {shown.next_steps.map((s, i) => (
            <div className="step-row" key={i}><span className="num">{i + 1}</span> {s}</div>
          ))}
        </div>
      )}
      {data.grounding?.reasons?.length > 0 && data.grounding.level !== 'well_grounded' && (
        <div className="ground-note">
          {data.grounding.reasons.map((r, i) => <p key={i}>{r}</p>)}
        </div>
      )}

      <p className="answer-disclaimer">
        Legal information drawn from public statutes and judgments, not legal advice.
        Check the linked sources or speak to a lawyer before you act.
      </p>
    </div>
  );
}

function DraftResult({ data, onCopy, copied, token }) {
  const [lang, setLang] = useState('en');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState('');
  const [versions, setVersions] = useState({ en: data.body });
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');

  const shown = versions[lang] ?? versions.en;

  async function pick(code) {
    setFailed('');
    if (code === lang || busy) return;
    if (versions[code] !== undefined) { setLang(code); return; }

    setBusy(true);
    try {
      const r = await translateText(token, { text: data.body, language: code });
      setVersions((v) => ({ ...v, [code]: r.body }));
      setLang(code);
    } catch {
      setFailed('That translation could not be produced. Showing English.');
    } finally {
      setBusy(false);
    }
  }

  async function handleDownload() {
    if (downloading || !data.query_log_id) return;
    setDownloadError('');
    setDownloading(true);
    try {
      await downloadDraftDocx(token, data.query_log_id);
    } catch (err) {
      setDownloadError(err.message || 'Could not download that draft.');
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="demo-card">
      <div className="demo-topbar">
        <div className="demo-brand"><span className="sq">न्या</span> Draft</div>
        <div className="demo-topbar-actions">
          <button type="button" className="copy-btn" onClick={() => onCopy(shown)}>
            {copied ? 'Copied' : 'Copy text'}
          </button>
          {/* query_log_id is only missing for a draft that predates this
              feature and was never reopened (so no id came back yet). */}
          <button
            type="button"
            className="copy-btn"
            onClick={handleDownload}
            disabled={downloading || !data.query_log_id}
            title={!data.query_log_id ? 'Reopen this draft once to enable downloading' : undefined}
          >
            {downloading ? 'Preparing…' : 'Download Word'}
          </button>
        </div>
      </div>

      {downloadError && <p className="lang-error">{downloadError}</p>}

      {data.needs_advocate && (
        <div className="advocate-warning">
          This is a court document. Have an advocate settle it before filing — a
          defective filing can be rejected or damage your case.
        </div>
      )}

      <h4 className="answer-title">{data.title}</h4>
      <pre className="draft-body">{shown}</pre>

      {/* Clause numbering, blanks and placeholders survive the translation -
          a draft that loses its structure is not a draft any more. */}
      <LanguageBar active={lang} busy={busy} onPick={pick} />
      {failed && <p className="lang-error">{failed}</p>}

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
            <a className="flag-cite" href={f.citation.url} target="_blank" rel="noopener noreferrer">
              {f.citation.title} &#8599;
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
        An automated first pass, not a lawyer&apos;s opinion. Have anything you&apos;re
        about to sign checked by an advocate.
      </p>
    </div>
  );
}