import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import {
  cancelConnection,
  fetchAdvocateFilters,
  searchAdvocates,
  sendConnectionRequest,
} from '../networkApi';
import { NetworkPage, refreshBadges } from '../components/AppShell';
import {
  Avatar,
  Chips,
  ConnectButton,
  ConnectDialog,
  ConnectionBadge,
  DemoBadge,
  Skeleton,
  locationOf,
} from '../components/NetworkBits';

const EXPERIENCE_STEPS = [
  { value: '', label: 'Any experience' },
  { value: '3', label: '3+ years' },
  { value: '5', label: '5+ years' },
  { value: '10', label: '10+ years' },
  { value: '15', label: '15+ years' },
  { value: '20', label: '20+ years' },
];

const BLANK = {
  q: '',
  city: '',
  state: '',
  specialization: '',
  court: '',
  language: '',
  min_experience: '',
};

export default function Advocates() {
  const { token, isAdvocate, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  // Filters live in the URL, so a search is shareable and the back button
  // returns to the results you were looking at rather than a blank page.
  const [form, setForm] = useState(() => ({
    ...BLANK,
    ...Object.fromEntries([...params.entries()]),
  }));
  const [page, setPage] = useState(() => Number(params.get('page')) || 1);

  const [result, setResult] = useState(null);
  const [options, setOptions] = useState({
    cities: [], states: [], specializations: [], courts: [], languages: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [dialogFor, setDialogFor] = useState(null);
  const [dialogError, setDialogError] = useState('');

  // Guards against a slow first request overwriting a fast second one - the
  // classic out-of-order search bug where you type, the old response lands
  // last, and the results no longer match the box.
  const reqRef = useRef(0);

  const run = useCallback(
    async (filters, pageNo) => {
      const seq = ++reqRef.current;
      setLoading(true);
      try {
        const data = await searchAdvocates(token, {
          ...filters,
          page: pageNo,
          per_page: 12,
        });
        if (seq !== reqRef.current) return;
        setResult(data);
        setError('');
      } catch (err) {
        if (seq !== reqRef.current) return;
        setError(err.message);
        setResult(null);
      } finally {
        if (seq === reqRef.current) setLoading(false);
      }
    },
    [token]
  );

  // An advocate reaching the directory by URL gets sent back rather than
  // shown a tool that isn't for them. Mirrors the guard on /matters, in the
  // other direction. `isAdvocate` is false while the profile is still
  // loading, so this waits for auth to settle first.
  useEffect(() => {
    if (!authLoading && isAdvocate) navigate('/network', { replace: true });
  }, [authLoading, isAdvocate, navigate]);

  useEffect(() => {
    if (authLoading || !token || isAdvocate) return;
    fetchAdvocateFilters(token).then(setOptions).catch(() => {});
  }, [authLoading, token, isAdvocate]);

  // Debounced: typing in the search box shouldn't fire a request per
  // keystroke, but a dropdown change should feel immediate, so 300ms is the
  // compromise rather than a submit button.
  useEffect(() => {
    if (authLoading || !token || isAdvocate) return undefined;
    const t = setTimeout(() => run(form, page), 300);
    return () => clearTimeout(t);
  }, [authLoading, token, isAdvocate, form, page, run]);

  useEffect(() => {
    const next = {};
    Object.entries(form).forEach(([k, v]) => {
      if (v) next[k] = v;
    });
    if (page > 1) next.page = String(page);
    setParams(next, { replace: true });
    // setParams identity changes every render in some router versions, so it
    // is deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form, page]);

  function update(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
    setPage(1);
  }

  const activeFilters = Object.entries(form).filter(([, v]) => v).length;

  async function doConnect(advocateUser, intro) {
    setBusyId(advocateUser.id);
    setDialogError('');
    try {
      await sendConnectionRequest(token, {
        receiver_id: advocateUser.id,
        intro_message: intro || null,
      });
      setDialogFor(null);
      await run(form, page);
      refreshBadges();
    } catch (err) {
      setDialogError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  async function doCancel(card) {
    const id = card.connection?.connection_id;
    if (!id) return;
    setBusyId(card.user.id);
    try {
      await cancelConnection(token, id);
      await run(form, page);
      refreshBadges();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  const items = result?.items || [];

  return (
    <NetworkPage active="advocates">
      {dialogFor && (
        <ConnectDialog
          advocate={dialogFor}
          busy={busyId === dialogFor.id}
          error={dialogError}
          onClose={() => {
            setDialogFor(null);
            setDialogError('');
          }}
          onSend={(intro) => doConnect(dialogFor, intro)}
        />
      )}

      <div className="page-scroll">
        <div className="page-wrap">
          <div className="page-head">
            <div>
              <h1>Find an Advocate</h1>
              <p className="page-sub">
                Search by name, city, practice area or experience, then send a
                connection request to start a private conversation.
              </p>
            </div>
          </div>

          <div className="nx-search-bar">
            <span className="nx-search-ic">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor"
                   strokeWidth="1.7" strokeLinecap="round">
                <circle cx="9" cy="9" r="5.5" />
                <path d="m13.5 13.5 3.5 3.5" />
              </svg>
            </span>
            <input
              className="nx-search-input"
              value={form.q}
              onChange={(e) => update('q', e.target.value)}
              placeholder="Try &ldquo;Criminal Lawyer&rdquo;, a name, or a firm&hellip;"
              aria-label="Search advocates"
            />
            {activeFilters > 0 && (
              <button
                type="button"
                className="btn btn-ghost sm"
                onClick={() => {
                  setForm(BLANK);
                  setPage(1);
                }}
              >
                Clear all
              </button>
            )}
          </div>

          <div className="nx-filter-row">
            <Select
              label="City"
              value={form.city}
              onChange={(v) => update('city', v)}
              options={options.cities}
              anyLabel="Any city"
            />
            <Select
              label="State"
              value={form.state}
              onChange={(v) => update('state', v)}
              options={options.states}
              anyLabel="Any state"
            />
            <Select
              label="Practice area"
              value={form.specialization}
              onChange={(v) => update('specialization', v)}
              options={options.specializations}
              anyLabel="All areas"
            />
            <Select
              label="Court"
              value={form.court}
              onChange={(v) => update('court', v)}
              options={options.courts}
              anyLabel="Any court"
            />
            <Select
              label="Language"
              value={form.language}
              onChange={(v) => update('language', v)}
              options={options.languages}
              anyLabel="Any language"
            />
            <div className="nx-filter">
              <label>Experience</label>
              <select
                value={form.min_experience}
                onChange={(e) => update('min_experience', e.target.value)}
              >
                {EXPERIENCE_STEPS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {error && <div className="form-error">{error}</div>}

          {!loading && result && (
            <p className="nx-result-count">
              {result.total === 0
                ? 'No advocates match those filters.'
                : `${result.total} advocate${result.total === 1 ? '' : 's'} found`}
            </p>
          )}

          {loading && !result ? (
            <Skeleton rows={4} />
          ) : items.length === 0 ? (
            <div className="nx-empty">
              <span className="nx-empty-ic" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     strokeWidth="1.5" strokeLinecap="round">
                  <circle cx="11" cy="11" r="6.5" />
                  <path d="m16 16 4.5 4.5" />
                </svg>
              </span>
              <h3 className="nx-empty-title">No matches</h3>
              <p className="nx-empty-text">
                Try a broader practice area, or clear the city filter.
              </p>
              {activeFilters > 0 && (
                <button
                  type="button"
                  className="btn btn-ghost sm"
                  onClick={() => {
                    setForm(BLANK);
                    setPage(1);
                  }}
                >
                  Clear all filters
                </button>
              )}
            </div>
          ) : (
            <div className={`nx-card-grid ${loading ? 'is-stale' : ''}`}>
              {items.map((card) => (
                <article className="nx-adv-card" key={card.user.id}>
                  <div className="nx-adv-top">
                    <Avatar user={card.user} size={54} />
                    <div className="nx-adv-ident">
                      <div className="nx-adv-name-row">
                        <h3>{card.user.name}</h3>
                        <DemoBadge user={card.user} />
                      </div>
                      <div className="nx-adv-sub">
                        {card.specialization || 'Advocate'}
                      </div>
                      <div className="nx-adv-meta">
                        {locationOf(card.user, card.practice_city)}
                        {card.years_experience != null && (
                          <>
                            <span className="nx-dot">&middot;</span>
                            {card.years_experience} yrs
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  {card.short_bio && <p className="nx-adv-bio">{card.short_bio}</p>}

                  <Chips value={card.practice_areas} max={3} />
                  {card.courts && (
                    <div className="nx-adv-courts">
                      <span className="nx-adv-label">Courts</span>
                      {card.courts}
                    </div>
                  )}

                  <div className="nx-adv-foot">
                    <ConnectionBadge connection={card.connection} />
                    <div className="nx-adv-actions">
                      <button
                        type="button"
                        className="btn btn-ghost sm"
                        onClick={() => navigate(`/advocates/${card.user.id}`)}
                      >
                        View profile
                      </button>
                      <ConnectButton
                        size="sm"
                        connection={card.connection}
                        busy={busyId === card.user.id}
                        onConnect={() => setDialogFor(card.user)}
                        onCancel={() => doCancel(card)}
                        onMessage={() =>
                          navigate(
                            card.connection?.thread_id
                              ? `/messages/${card.connection.thread_id}`
                              : '/messages'
                          )
                        }
                      />
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}

          {result && result.pages > 1 && (
            <div className="nx-pager">
              <button
                type="button"
                className="btn btn-ghost sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>
              <span className="nx-pager-label">
                Page {result.page} of {result.pages}
              </span>
              <button
                type="button"
                className="btn btn-ghost sm"
                disabled={page >= result.pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          )}
        </div>
      </div>
    </NetworkPage>
  );
}

function Select({ label, value, onChange, options, anyLabel }) {
  return (
    <div className="nx-filter">
      <label>{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{anyLabel}</option>
        {(options || []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}