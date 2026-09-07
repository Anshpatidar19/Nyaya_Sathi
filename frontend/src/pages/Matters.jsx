import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import {
  createMatter,
  fetchMatters,
  fetchUpcomingEvents,
  invalidateReads,
} from '../api';
import { AppNav, AppSidebarUser, AppTopbar } from '../components/AppShell';
import MatterCalendar, { formatDate } from '../components/MatterCalendar';

const SIDES = [
  'Petitioner', 'Respondent', 'Plaintiff', 'Defendant',
  'Appellant', 'Complainant', 'Accused', 'Applicant',
];

const FOLDER = (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M2.5 6.5a1.5 1.5 0 0 1 1.5-1.5h3l1.5 2h6a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5z" />
  </svg>
);

const TABS = [
  { id: 'all', label: 'All' },
  { id: 'active', label: 'Active' },
  { id: 'archived', label: 'Archived' },
];

export default function Matters() {
  const { token, isAdvocate, loading: authLoading } = useAuth();
  const navigate = useNavigate();

  const [matters, setMatters] = useState([]);
  const [events, setEvents] = useState([]);
  const [tab, setTab] = useState('all');
  const [query, setQuery] = useState('');
  const [selectedDate, setSelectedDate] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem('ns_sidebar') !== 'closed'
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showNew, setShowNew] = useState(false);

  useEffect(() => {
    localStorage.setItem('ns_sidebar', sidebarOpen ? 'open' : 'closed');
  }, [sidebarOpen]);

  // A general user reaching /matters by URL gets sent back rather than
  // shown an empty shell of a tool they don't have.
  // `isAdvocate` is false while the profile is still being fetched, not
  // undefined - so redirecting on `=== false` bounced every advocate straight
  // back to Ask before auth had even resolved. Wait for it to settle.
  useEffect(() => {
    if (!authLoading && !isAdvocate) navigate('/ask', { replace: true });
  }, [authLoading, isAdvocate, navigate]);

  useEffect(() => {
    if (authLoading || !token || !isAdvocate) return;
    load();
    // `tab` is deliberately NOT a dependency. Switching All/Active/Archived
    // used to refire both network calls; the full list is small, so it is
    // fetched once and filtered in memory instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, token, isAdvocate]);

  async function load() {
    setLoading(true);
    try {
      // Matters render as soon as they arrive. The calendar fills in after,
      // rather than holding the whole page behind the slower of two calls.
      const m = await fetchMatters(token);
      setMatters(m);
      setError('');
      setLoading(false);
      fetchUpcomingEvents(token, 120).then(setEvents).catch(() => {});
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  }

  // Filtering client-side keeps typing instant; the list is small enough
  // that a round trip per keystroke would be the slower option.
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    let rows = tab === 'all' ? matters : matters.filter((m) => m.status === tab);
    if (q) {
      rows = rows.filter((m) =>
        [m.title, m.client_name, m.case_number, m.court, m.tags]
          .filter(Boolean)
          .some((f) => f.toLowerCase().includes(q))
      );
    }
    return rows;
  }, [matters, query, tab]);

  const dayEvents = useMemo(
    () => (selectedDate ? events.filter((e) => e.event_date === selectedDate) : []),
    [events, selectedDate]
  );

  const matterName = (id) => matters.find((m) => m.id === id)?.title || 'Matter';

  const counts = useMemo(() => ({
    all: matters.length,
    active: matters.filter((m) => m.status === 'active').length,
    archived: matters.filter((m) => m.status === 'archived').length,
  }), [matters]);

  return (
    <div className="ask-app">
      {showNew && (
        <NewMatterDialog
          token={token}
          onClose={() => setShowNew(false)}
          onCreated={(m) => {
            // The list is cached briefly; a new matter must not be missing
            // from it when the user comes back.
            invalidateReads('/matters');
            setShowNew(false);
            navigate(`/matters/${m.id}`);
          }}
        />
      )}

      <AppTopbar sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((o) => !o)} />

      <main className="ask-shell">
        <div className="ask-layout">
          <aside className={`ask-sidebar ${sidebarOpen ? '' : 'collapsed'}`}>
            <button type="button" className="new-chat-btn" onClick={() => setShowNew(true)}>
              <span className="plus-ic">+</span> New matter
            </button>
            <AppNav active="matters" />
            <div className="ask-sidebar-divider" />
            <h4>Your matters</h4>
            <div className="ask-history-list">
              {matters.length === 0 && !loading && (
                <div className="history-empty">No matters yet.</div>
              )}
              {matters.slice(0, 20).map((m) => (
                <div
                  className="history-item"
                  key={m.id}
                  onClick={() => navigate(`/matters/${m.id}`)}
                >
                  <span className="history-item-text">{m.title}</span>
                </div>
              ))}
            </div>
            <AppSidebarUser />
          </aside>

          <div className="ask-main">
            <div className="page-scroll">
              <div className="page-wrap">
                <div className="page-head">
                  <div>
                    <h1>Matters</h1>
                    <p className="page-sub">
                      Your case files — hearings, notes, papers and research in one place.
                    </p>
                  </div>
                  <button type="button" className="btn btn-primary" onClick={() => setShowNew(true)}>
                    + New matter
                  </button>
                </div>

                <div className="seg-tabs">
                  {TABS.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className={`seg-tab ${tab === t.id ? 'active' : ''}`}
                      onClick={() => setTab(t.id)}
                    >
                      {t.label}
                      <span className="seg-count">{counts[t.id]}</span>
                    </button>
                  ))}
                </div>

                {error && <div className="form-error">{error}</div>}

                <div className="matters-layout">
                  <div className="matters-col">
                    <input
                      className="matter-search"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Search by case, client, number or court…"
                    />

                    {loading ? (
                      <div className="matter-list">
                        {[0, 1, 2].map((i) => (
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
                    ) : visible.length === 0 ? (
                      <div className="page-empty">
                        <p>{query ? 'Nothing matches that search.' : 'No matters here yet.'}</p>
                        {!query && (
                          <button type="button" className="btn btn-ghost" onClick={() => setShowNew(true)}>
                            Create your first matter
                          </button>
                        )}
                      </div>
                    ) : (
                      <div className="matter-list">
                        {visible.map((m) => (
                          <MatterRow key={m.id} matter={m} onOpen={() => navigate(`/matters/${m.id}`)} />
                        ))}
                      </div>
                    )}
                  </div>

                  <aside className="matters-side">
                    <div className="side-label">Upcoming hearings</div>
                    <MatterCalendar
                      events={events}
                      selected={selectedDate}
                      onSelect={setSelectedDate}
                    />

                    <div className="upcoming-list">
                      {selectedDate ? (
                        dayEvents.length === 0 ? (
                          <p className="upcoming-empty">
                            Nothing on {formatDate(selectedDate)}.
                          </p>
                        ) : (
                          dayEvents.map((e) => (
                            <button
                              type="button"
                              className="upcoming-row"
                              key={e.id}
                              onClick={() => navigate(`/matters/${e.matter_id}`)}
                            >
                              <span className={`ev-kind k-${e.kind}`}>{e.kind}</span>
                              <span className="upcoming-title">{e.title}</span>
                              <span className="upcoming-meta">{matterName(e.matter_id)}</span>
                            </button>
                          ))
                        )
                      ) : events.length === 0 ? (
                        <p className="upcoming-empty">No upcoming hearings.</p>
                      ) : (
                        events.slice(0, 6).map((e) => (
                          <button
                            type="button"
                            className="upcoming-row"
                            key={e.id}
                            onClick={() => navigate(`/matters/${e.matter_id}`)}
                          >
                            <span className="upcoming-date">{formatDate(e.event_date)}</span>
                            <span className="upcoming-title">{e.title}</span>
                            <span className="upcoming-meta">{matterName(e.matter_id)}</span>
                          </button>
                        ))
                      )}
                    </div>
                  </aside>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function MatterRow({ matter, onOpen }) {
  const tags = (matter.tags || '').split(',').map((t) => t.trim()).filter(Boolean);
  return (
    <button type="button" className="matter-row" onClick={onOpen}>
      <span className="matter-ic">{FOLDER}</span>
      <span className="matter-body">
        <span className="matter-row-head">
          <span className="matter-title">{matter.title}</span>
          {matter.urgent && <span className="tag urgent">Urgent</span>}
          <span className={`status-chip s-${matter.status}`}>{matter.status}</span>
        </span>
        <span className="matter-meta">
          {matter.case_number && <span>{matter.case_number}</span>}
          {matter.client_name && <span>Client: {matter.client_name}</span>}
          {matter.court && <span>{matter.court}</span>}
        </span>
        {matter.description && <span className="matter-desc">{matter.description}</span>}
        <span className="matter-foot">
          {tags.map((t) => <span className="tag" key={t}>{t}</span>)}
          {matter.next_hearing && (
            <span className="tag next">
              Next: {formatDate(matter.next_hearing.event_date)}
            </span>
          )}
          <span className="matter-counts">
            {matter.document_count} docs · {matter.note_count} notes · {matter.research_count} research
          </span>
        </span>
      </span>
    </button>
  );
}

function NewMatterDialog({ token, onClose, onCreated }) {
  const [form, setForm] = useState({
    title: '', client_name: '', case_number: '', court: '',
    side: '', description: '', tags: '', urgent: false,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e) {
    e.preventDefault();
    if (!form.title.trim()) return;
    setSaving(true);
    setError('');
    try {
      onCreated(await createMatter(token, form));
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <div className="arg-setup-backdrop" role="dialog" aria-modal="true">
      <form className="arg-setup" onSubmit={submit}>
        <button type="button" className="arg-setup-x" onClick={onClose} aria-label="Close">×</button>
        <h2>New matter</h2>
        <p className="arg-setup-sub">
          Only the case title is required — everything else can be filled in later.
        </p>

        {error && <div className="form-error">{error}</div>}

        <div className="field">
          <label htmlFor="m-title">Case title</label>
          <input
            id="m-title" required autoFocus value={form.title}
            onChange={(e) => set('title', e.target.value)}
            placeholder="e.g. Meridian Textiles v. Sundaram Estates"
          />
        </div>

        <div className="arg-setup-row">
          <div className="field">
            <label htmlFor="m-client">Client</label>
            <input id="m-client" value={form.client_name}
                   onChange={(e) => set('client_name', e.target.value)}
                   placeholder="Who you act for" />
          </div>
          <div className="field">
            <label htmlFor="m-number">Case number</label>
            <input id="m-number" value={form.case_number}
                   onChange={(e) => set('case_number', e.target.value)}
                   placeholder="e.g. CS 118/2026" />
          </div>
        </div>

        <div className="arg-setup-row">
          <div className="field">
            <label htmlFor="m-court">Court or forum</label>
            <input id="m-court" value={form.court}
                   onChange={(e) => set('court', e.target.value)}
                   placeholder="e.g. District Judge, Jabalpur" />
          </div>
          <div className="field">
            <label htmlFor="m-side">You appear for</label>
            <select id="m-side" value={form.side} onChange={(e) => set('side', e.target.value)}>
              <option value="">Not set</option>
              {SIDES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>

        <div className="field">
          <label htmlFor="m-desc">Description</label>
          <input id="m-desc" value={form.description}
                 onChange={(e) => set('description', e.target.value)}
                 placeholder="One line on what the case is about" />
        </div>

        <div className="arg-setup-row">
          <div className="field">
            <label htmlFor="m-tags">Tags</label>
            <input id="m-tags" value={form.tags}
                   onChange={(e) => set('tags', e.target.value)}
                   placeholder="Civil, Lease — comma separated" />
          </div>
          <div className="field">
            <label htmlFor="m-urgent">Priority</label>
            <label className="check-row">
              <input id="m-urgent" type="checkbox" checked={form.urgent}
                     onChange={(e) => set('urgent', e.target.checked)} />
              Mark as urgent
            </label>
          </div>
        </div>

        <button className="btn btn-primary btn-block" type="submit" disabled={saving || !form.title.trim()}>
          {saving ? <span className="spinner" /> : 'Create matter'}
        </button>
      </form>
    </div>
  );
}