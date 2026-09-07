import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import {
  createMatterEvent,
  createMatterNote,
  deleteMatter,
  deleteMatterDocument,
  deleteMatterEvent,
  deleteMatterNote,
  fetchDocumentUrl,
  fetchMatter,
  invalidateReads,
  updateMatter,
  updateMatterEvent,
  uploadMatterDocument,
} from '../api';
import { AppNav, AppSidebarUser, AppTopbar } from '../components/AppShell';
import { formatDate, isoDate } from '../components/MatterCalendar';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'notes', label: 'Notes' },
  { id: 'documents', label: 'Documents' },
  { id: 'research', label: 'Research' },
];

const EVENT_KINDS = [
  { id: 'hearing', label: 'Hearing' },
  { id: 'filing', label: 'Filing' },
  { id: 'deadline', label: 'Deadline' },
  { id: 'meeting', label: 'Client meeting' },
  { id: 'other', label: 'Other' },
];

const SIDES = [
  'Petitioner', 'Respondent', 'Plaintiff', 'Defendant',
  'Appellant', 'Complainant', 'Accused', 'Applicant',
];

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export default function MatterDetail() {
  const { id } = useParams();
  const { token, isAdvocate, loading: authLoading } = useAuth();
  const navigate = useNavigate();

  const [matter, setMatter] = useState(null);
  const [tab, setTab] = useState('overview');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem('ns_sidebar') !== 'closed'
  );

  // `isAdvocate` is false while the profile is still being fetched, not
  // undefined - so redirecting on `=== false` bounced every advocate straight
  // back to Ask before auth had even resolved. Wait for it to settle.
  useEffect(() => {
    if (!authLoading && !isAdvocate) navigate('/ask', { replace: true });
  }, [authLoading, isAdvocate, navigate]);

  useEffect(() => {
    if (authLoading || !token || !isAdvocate) return;
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, token, isAdvocate, id]);

  async function reload() {
    try {
      // Always fresh: this runs right after a write, so the short-lived GET
      // cache would otherwise hand back the pre-edit version.
      invalidateReads('/matters');
      setMatter(await fetchMatter(token, id, { fresh: true }));
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      // Only the first load blocks. Later reloads - after adding a note or a
      // timeline entry - swap the data underneath without flashing a spinner
      // over a page that is already on screen.
      setLoading(false);
    }
  }

  async function patch(payload) {
    try {
      await updateMatter(token, id, payload);
      reload();
    } catch (err) {
      setError(err.message);
    }
  }

  async function removeMatter() {
    if (!window.confirm('Delete this matter? Its timeline and notes go with it. Documents and research are kept and just unfiled.')) return;
    try {
      await deleteMatter(token, id);
      navigate('/matters');
    } catch (err) {
      setError(err.message);
    }
  }

  if (loading) {
    return (
      <div className="ask-app">
        <AppTopbar sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((o) => !o)} />
        <main className="ask-shell">
          <div className="page-empty"><span className="spinner spinner-dark" /></div>
        </main>
      </div>
    );
  }

  if (!matter) {
    return (
      <div className="ask-app">
        <AppTopbar sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((o) => !o)} />
        <main className="ask-shell">
          <div className="page-empty">
            <p>{error || "That matter doesn't exist, or isn't yours."}</p>
            <button type="button" className="btn btn-ghost" onClick={() => navigate('/matters')}>
              Back to matters
            </button>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="ask-app">
      <AppTopbar sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((o) => !o)} />

      <main className="ask-shell">
        <div className="ask-layout">
          <aside className={`ask-sidebar ${sidebarOpen ? '' : 'collapsed'}`}>
            <button type="button" className="new-chat-btn" onClick={() => navigate('/matters')}>
              ← All matters
            </button>
            <AppNav active="matters" />
            <div className="ask-sidebar-divider" />
            <h4>This matter</h4>
            <div className="ask-history-list">
              {TABS.map((t) => (
                <div
                  className={`history-item ${tab === t.id ? 'active' : ''}`}
                  key={t.id}
                  onClick={() => setTab(t.id)}
                >
                  <span className="history-item-text">{t.label}</span>
                </div>
              ))}
            </div>
            <AppSidebarUser />
          </aside>

          <div className="ask-main">
            <div className="page-scroll">
              <div className="page-wrap">
                <div className="matter-head">
                  <button type="button" className="back-btn" onClick={() => navigate('/matters')} aria-label="Back">←</button>
                  <div className="matter-head-text">
                    <h1>{matter.title}</h1>
                    <p className="matter-head-sub">
                      {matter.client_name && <>{matter.client_name} · </>}
                      <span className={`status-chip s-${matter.status}`}>{matter.status}</span>
                      {matter.urgent && <span className="tag urgent">Urgent</span>}
                    </p>
                  </div>
                  <div className="matter-head-actions">
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => patch({ status: matter.status === 'active' ? 'archived' : 'active' })}
                    >
                      {matter.status === 'active' ? 'Archive' : 'Reopen'}
                    </button>
                    <button type="button" className="btn btn-ghost danger" onClick={removeMatter}>
                      Delete
                    </button>
                  </div>
                </div>

                <div className="matter-tabs">
                  {TABS.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className={`matter-tab ${tab === t.id ? 'active' : ''}`}
                      onClick={() => setTab(t.id)}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>

                {error && <div className="form-error">{error}</div>}

                {tab === 'overview' && <Overview matter={matter} onSave={patch} />}
                {tab === 'timeline' && <Timeline matter={matter} token={token} onChange={reload} />}
                {tab === 'notes' && <Notes matter={matter} token={token} onChange={reload} />}
                {tab === 'documents' && <Documents matter={matter} token={token} onChange={reload} />}
                {tab === 'research' && <Research matter={matter} />}
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

/* ---------------- Overview ---------------- */

function Overview({ matter, onSave }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    title: matter.title || '',
    client_name: matter.client_name || '',
    case_number: matter.case_number || '',
    court: matter.court || '',
    side: matter.side || '',
    description: matter.description || '',
    notes: matter.notes || '',
    tags: matter.tags || '',
    urgent: !!matter.urgent,
  });

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const tags = (matter.tags || '').split(',').map((t) => t.trim()).filter(Boolean);

  if (editing) {
    return (
      <div className="panel">
        <div className="arg-setup-row">
          <div className="field">
            <label>Case title</label>
            <input value={form.title} onChange={(e) => set('title', e.target.value)} />
          </div>
          <div className="field">
            <label>Client</label>
            <input value={form.client_name} onChange={(e) => set('client_name', e.target.value)} />
          </div>
        </div>
        <div className="arg-setup-row">
          <div className="field">
            <label>Case number</label>
            <input value={form.case_number} onChange={(e) => set('case_number', e.target.value)} />
          </div>
          <div className="field">
            <label>Court or forum</label>
            <input value={form.court} onChange={(e) => set('court', e.target.value)} />
          </div>
        </div>
        <div className="arg-setup-row">
          <div className="field">
            <label>You appear for</label>
            <select value={form.side} onChange={(e) => set('side', e.target.value)}>
              <option value="">Not set</option>
              {SIDES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Tags</label>
            <input value={form.tags} onChange={(e) => set('tags', e.target.value)}
                   placeholder="Comma separated" />
          </div>
        </div>
        <div className="field">
          <label>Description</label>
          <input value={form.description} onChange={(e) => set('description', e.target.value)} />
        </div>
        <div className="field">
          <label>Matter notes</label>
          <textarea className="review-input" rows={4} value={form.notes}
                    onChange={(e) => set('notes', e.target.value)}
                    placeholder="Standing notes about the matter — strategy, contacts, anything you want on the file." />
        </div>
        <label className="check-row">
          <input type="checkbox" checked={form.urgent} onChange={(e) => set('urgent', e.target.checked)} />
          Mark as urgent
        </label>
        <div className="panel-actions">
          <button type="button" className="btn btn-ghost" onClick={() => setEditing(false)}>Cancel</button>
          <button type="button" className="btn btn-primary"
                  onClick={() => { onSave(form); setEditing(false); }}>
            Save changes
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="stat-row">
        <Stat label="Timeline entries" value={matter.event_count} />
        <Stat label="Documents" value={matter.document_count} />
        <Stat label="Hearing notes" value={matter.note_count} />
        <Stat
          label="Next hearing"
          value={matter.next_hearing ? formatDate(matter.next_hearing.event_date) : '—'}
          sub={matter.next_hearing?.title}
        />
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3>Details</h3>
          <button type="button" className="link-btn" onClick={() => setEditing(true)}>Edit</button>
        </div>
        <dl className="arg-overview">
          <dt>Client</dt><dd>{matter.client_name || '—'}</dd>
          <dt>Case number</dt><dd>{matter.case_number || '—'}</dd>
          <dt>Court</dt><dd>{matter.court || '—'}</dd>
          <dt>Appearing for</dt><dd>{matter.side || '—'}</dd>
          <dt>Description</dt><dd>{matter.description || '—'}</dd>
        </dl>
        {tags.length > 0 && (
          <div className="matter-foot">
            {tags.map((t) => <span className="tag" key={t}>{t}</span>)}
          </div>
        )}
      </div>

      {matter.notes && (
        <div className="panel">
          <div className="panel-head"><h3>Matter notes</h3></div>
          <p className="note-body">{matter.notes}</p>
        </div>
      )}
    </>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="stat-card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

/* ---------------- Timeline ---------------- */

function Timeline({ matter, token, onChange }) {
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({
    kind: 'hearing', title: '', event_date: isoDate(new Date()),
    event_time: '', location: '', notes: '',
  });
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const today = isoDate(new Date());
  const upcoming = matter.events.filter((e) => e.event_date >= today && !e.done);
  const past = matter.events.filter((e) => e.event_date < today || e.done)
    .sort((a, b) => (a.event_date < b.event_date ? 1 : -1));

  async function add() {
    if (!form.title.trim()) return;
    setBusy(true);
    try {
      await createMatterEvent(token, matter.id, form);
      setForm({ kind: 'hearing', title: '', event_date: today, event_time: '', location: '', notes: '' });
      setAdding(false);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  async function toggleDone(e) {
    await updateMatterEvent(token, matter.id, e.id, { done: !e.done });
    onChange();
  }

  async function remove(e) {
    if (!window.confirm('Remove this entry?')) return;
    await deleteMatterEvent(token, matter.id, e.id);
    onChange();
  }

  return (
    <>
      <div className="panel-head standalone">
        <h3>Timeline</h3>
        <button type="button" className="btn btn-primary sm" onClick={() => setAdding((a) => !a)}>
          {adding ? 'Cancel' : '+ Add date'}
        </button>
      </div>

      {adding && (
        <div className="panel">
          <div className="arg-setup-row">
            <div className="field">
              <label>What is it</label>
              <select value={form.kind} onChange={(e) => set('kind', e.target.value)}>
                {EVENT_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
              </select>
            </div>
            <div className="field">
              <label>Date</label>
              <input type="date" value={form.event_date}
                     onChange={(e) => set('event_date', e.target.value)} />
            </div>
          </div>
          <div className="field">
            <label>Title</label>
            <input value={form.title} onChange={(e) => set('title', e.target.value)}
                   placeholder="e.g. Arguments on interim application" />
          </div>
          <div className="arg-setup-row">
            <div className="field">
              <label>Time</label>
              <input value={form.event_time} onChange={(e) => set('event_time', e.target.value)}
                     placeholder="e.g. 11:00 AM" />
            </div>
            <div className="field">
              <label>Where</label>
              <input value={form.location} onChange={(e) => set('location', e.target.value)}
                     placeholder="e.g. Court No. 4" />
            </div>
          </div>
          <div className="field">
            <label>Notes</label>
            <input value={form.notes} onChange={(e) => set('notes', e.target.value)}
                   placeholder="Anything to remember" />
          </div>
          <div className="panel-actions">
            <button type="button" className="btn btn-primary" onClick={add}
                    disabled={busy || !form.title.trim()}>
              {busy ? <span className="spinner" /> : 'Add to timeline'}
            </button>
          </div>
        </div>
      )}

      {matter.events.length === 0 && !adding && (
        <div className="page-empty"><p>Nothing scheduled yet.</p></div>
      )}

      {upcoming.length > 0 && (
        <>
          <div className="section-label">Upcoming</div>
          <div className="event-list">
            {upcoming.map((e) => (
              <EventRow key={e.id} event={e} onToggle={toggleDone} onRemove={remove} />
            ))}
          </div>
        </>
      )}

      {past.length > 0 && (
        <>
          <div className="section-label">Past and completed</div>
          <div className="event-list">
            {past.map((e) => (
              <EventRow key={e.id} event={e} past onToggle={toggleDone} onRemove={remove} />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function EventRow({ event, past, onToggle, onRemove }) {
  return (
    <div className={`event-row ${past ? 'past' : ''} ${event.done ? 'done' : ''}`}>
      <div className="event-date">
        <span className="ev-day">{formatDate(event.event_date)}</span>
        {event.event_time && <span className="ev-time">{event.event_time}</span>}
      </div>
      <div className="event-body">
        <div className="event-title-row">
          <span className={`ev-kind k-${event.kind}`}>{event.kind}</span>
          <span className="event-title">{event.title}</span>
        </div>
        {event.location && <span className="event-meta">{event.location}</span>}
        {event.notes && <p className="event-notes">{event.notes}</p>}
      </div>
      <div className="event-actions">
        <button type="button" className="link-btn" onClick={() => onToggle(event)}>
          {event.done ? 'Reopen' : 'Done'}
        </button>
        <button type="button" className="link-btn danger" onClick={() => onRemove(event)}>
          Remove
        </button>
      </div>
    </div>
  );
}

/* ---------------- Notes ---------------- */

function Notes({ matter, token, onChange }) {
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ note_date: isoDate(new Date()), title: '', body: '' });
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function add() {
    if (!form.body.trim()) return;
    setBusy(true);
    try {
      await createMatterNote(token, matter.id, form);
      setForm({ note_date: isoDate(new Date()), title: '', body: '' });
      setAdding(false);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  async function remove(n) {
    if (!window.confirm('Delete this note?')) return;
    await deleteMatterNote(token, matter.id, n.id);
    onChange();
  }

  return (
    <>
      <div className="panel-head standalone">
        <h3>Hearing notes</h3>
        <button type="button" className="btn btn-primary sm" onClick={() => setAdding((a) => !a)}>
          {adding ? 'Cancel' : '+ Add note'}
        </button>
      </div>
      <p className="page-sub tight">
        What actually happened on the day — orders passed, what the bench said, what to do next.
      </p>

      {adding && (
        <div className="panel">
          <div className="arg-setup-row">
            <div className="field">
              <label>Date</label>
              <input type="date" value={form.note_date}
                     onChange={(e) => set('note_date', e.target.value)} />
            </div>
            <div className="field">
              <label>Heading (optional)</label>
              <input value={form.title} onChange={(e) => set('title', e.target.value)}
                     placeholder="e.g. Adjourned — reply not filed" />
            </div>
          </div>
          <div className="field">
            <label>What happened</label>
            <textarea className="review-input" rows={6} value={form.body}
                      onChange={(e) => set('body', e.target.value)}
                      placeholder="Matter was called at 11:20. Other side sought time to file reply…" />
          </div>
          <div className="panel-actions">
            <button type="button" className="btn btn-primary" onClick={add}
                    disabled={busy || !form.body.trim()}>
              {busy ? <span className="spinner" /> : 'Save note'}
            </button>
          </div>
        </div>
      )}

      {matter.notes_entries.length === 0 && !adding ? (
        <div className="page-empty"><p>No notes yet.</p></div>
      ) : (
        <div className="note-list">
          {matter.notes_entries.map((n) => (
            <div className="note-card" key={n.id}>
              <div className="note-head">
                <span className="note-date">{formatDate(n.note_date)}</span>
                {n.title && <span className="note-title">{n.title}</span>}
                <button type="button" className="link-btn danger" onClick={() => remove(n)}>
                  Delete
                </button>
              </div>
              <p className="note-body">{n.body}</p>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ---------------- Documents ---------------- */

function Documents({ matter, token, onChange }) {
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');

  async function upload(file) {
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      setError('That file is larger than 10 MB.');
      return;
    }
    setError('');
    setUploading(true);
    try {
      await uploadMatterDocument(token, matter.id, file);
      onChange();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function open(doc) {
    try {
      const { url } = await fetchDocumentUrl(token, doc.id);
      window.open(url, '_blank', 'noopener');
    } catch (err) {
      setError(err.message);
    }
  }

  async function remove(doc) {
    if (!window.confirm(`Delete ${doc.filename}? This removes it from storage too.`)) return;
    try {
      await deleteMatterDocument(token, matter.id, doc.id);
      onChange();
    } catch (err) {
      setError(err.message);
    }
  }

  const kb = (n) => (n ? `${(n / 1024 / 1024).toFixed(1)} MB` : '');

  return (
    <>
      <div className="panel-head standalone">
        <h3>Documents</h3>
      </div>

      {error && <div className="form-error">{error}</div>}

      <label
        className={`dropzone ${dragging ? 'over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          upload(e.dataTransfer.files?.[0]);
        }}
      >
        <input
          type="file"
          style={{ display: 'none' }}
          accept=".pdf,.doc,.docx,.txt,.jpg,.jpeg,.png,.webp"
          onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ''; }}
        />
        <span className="dropzone-ic">
          {uploading ? <span className="spinner spinner-dark" /> : '⬆'}
        </span>
        <span className="dropzone-text">
          {uploading ? 'Uploading…' : 'Drop a file here, or click to browse'}
        </span>
        <span className="dropzone-hint">PDF, Word, text or image · up to 10 MB</span>
      </label>

      {matter.documents.length === 0 ? (
        <div className="page-empty"><p>No documents filed under this matter.</p></div>
      ) : (
        <div className="doc-list">
          <div className="section-label">
            {matter.documents.length} document{matter.documents.length === 1 ? '' : 's'}
          </div>
          {matter.documents.map((d) => (
            <div className="doc-row" key={d.id}>
              <span className="doc-ic">📄</span>
              <span className="doc-body">
                <span className="doc-name">{d.filename}</span>
                <span className="doc-meta">
                  {kb(d.size_bytes)}
                  {d.note ? ` · ${d.note}` : ''}
                </span>
              </span>
              <button type="button" className="link-btn" onClick={() => open(d)}>Open</button>
              <button type="button" className="link-btn danger" onClick={() => remove(d)}>Delete</button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ---------------- Research ---------------- */

function Research({ matter }) {
  const navigate = useNavigate();

  return (
    <>
      <div className="panel-head standalone">
        <h3>Research on this matter</h3>
      </div>
      <p className="page-sub tight">
        Ask, Draft, Review and Arguments threads filed under this case.
      </p>

      {matter.research.length === 0 ? (
        <div className="page-empty">
          <p>Nothing yet. Research started from this matter will appear here.</p>
          <button type="button" className="btn btn-ghost" onClick={() => navigate('/ask')}>
            Go to Ask
          </button>
        </div>
      ) : (
        <div className="doc-list">
          {matter.research.map((c) => (
            <button type="button" className="doc-row as-btn" key={c.id} onClick={() => navigate('/ask')}>
              <span className={`ev-kind k-${c.mode}`}>{c.mode}</span>
              <span className="doc-body">
                <span className="doc-name">{c.title}</span>
                <span className="doc-meta">{new Date(c.updated_at).toLocaleDateString()}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}