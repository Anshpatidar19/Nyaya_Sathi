/* The answer card while it is still being produced.

   Shared by Ask and by a matter's own Research page, so both show the same
   progress readout.

   Every step on the stepper corresponds to a `status` event the backend
   sends at the moment that work actually begins (see reasoning.Reporter):

     search    local BM25 pass over the bare acts
     fetch     statute text, the Kanoon search, judgment fragments
     analyze   synthesis has started - the model is reading the sources
     generate  the first word of the answer has arrived
     finalize  prose finished - citations and grounding being assembled

   Nothing here advances on a timer, and no elapsed-seconds counter is
   shown: the stepper and the server's detail line already say what is
   happening, which is what a reader waiting on a search needs. If a step is skipped (a greeting has
   no retrieval at all), the stepper jumps, because that is what happened.
   The detail line under the steps is the server's own sentence - "Reading
   2 relevant judgments" - so it says what is being done, not what might be.

   Citations, next steps and the grounding badge stay absent until the
   answer is finished - showing placeholders for them would be a lie. */

import '../thinking.css';

export const STAGES = [
  { id: 'search', label: 'Searching database' },
  { id: 'fetch', label: 'Fetching relevant information' },
  { id: 'analyze', label: 'Analyzing results' },
  { id: 'generate', label: 'Generating answer' },
  { id: 'finalize', label: 'Finalizing response' },
];

/* One stroked line icon per step, drawn in currentColor so it follows the
   step's state (faded, active, done) and the dark theme. Emoji rendered
   differently on every OS and read as decoration; these match the rest of
   the app's icon set.

   Each icon has a moving part that animates only while its step is the
   active one (thinking.css, .think-step.is-active):
     search    the lens sweeps, scanning
     fetch     text lines write themselves onto the page
     analyze   the bars of a chart rise and settle in turn
     generate  the pen writes a line
     finalize  a ring closes and the tick draws
   pathLength="1" lets the CSS draw any path with one dash value. */
const ICONS = {
  search: (
    <g className="ic-search">
      <g className="ic-lens">
        <circle cx="8.5" cy="8.5" r="4.75" />
        <path d="M6.6 6.9a2.6 2.6 0 0 1 2.3-1" className="ic-glint" />
      </g>
      <path d="M12.1 12.1 16.5 16.5" />
    </g>
  ),
  fetch: (
    <g className="ic-fetch">
      <path d="M5 2.75h6.25L15 6.5v10.75H5z" />
      <path d="M11 2.75V6.5h4" />
      <path className="ic-line l1" pathLength="1" d="M7.5 9.75h5" />
      <path className="ic-line l2" pathLength="1" d="M7.5 12.25h5" />
      <path className="ic-line l3" pathLength="1" d="M7.5 14.75h3" />
    </g>
  ),
  analyze: (
    <g className="ic-analyze">
      <path d="M3 17h14" />
      <rect className="ic-bar b1" x="4.25" y="10" width="2.5" height="7" rx="0.6" />
      <rect className="ic-bar b2" x="8.75" y="6" width="2.5" height="11" rx="0.6" />
      <rect className="ic-bar b3" x="13.25" y="3" width="2.5" height="14" rx="0.6" />
    </g>
  ),
  generate: (
    <g className="ic-generate">
      <g className="ic-pen">
        <path d="M12.9 3.6 15.9 6.6 8.2 14.3 4.6 15.2 5.5 11.6z" />
        <path d="M11.3 5.2 14.3 8.2" />
      </g>
      <path className="ic-ink" pathLength="1" d="M3 18.2c1.6-1.1 2.8 1 4.4 0s2.8 1 4.4 0 2.8 1 4.2 0" />
    </g>
  ),
  finalize: (
    <g className="ic-finalize">
      <circle className="ic-ring" pathLength="1" cx="10" cy="10" r="7.25" />
      <path className="ic-tick" pathLength="1" d="M6.9 10.3 9 12.4l4.2-4.6" />
    </g>
  ),
};

function StepIcon({ id }) {
  return (
    <svg
      className="think-step-svg"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[id]}
    </svg>
  );
}

const INDEX = Object.fromEntries(STAGES.map((s, i) => [s.id, i]));

export default function StreamingAnswer({ body, status, brand = 'Research' }) {
  const text = body || '';
  const writing = text.trim().length > 0;
  const paragraphs = text.split(/\n{2,}/);

  // The furthest stage reached. Text on screen means generation has begun
  // even if its status frame was lost - the fast greeting path sends none.
  let current = status?.stage in INDEX ? INDEX[status.stage] : -1;
  if (writing && current < INDEX.generate) current = INDEX.generate;

  const active = current >= 0 ? STAGES[current] : null;
  const detail =
    (status && INDEX[status.stage] === current && status.detail) ||
    (active ? active.label : 'Preparing your question');

  return (
    <div className="demo-card think-card">
      <span className="think-rail" aria-hidden="true" />

      <div className="demo-topbar">
        <div className="demo-brand"><span className="sq">न्या</span> {brand}</div>
        <span className={`think-pill${writing ? ' is-writing' : ''}`}>
          <span className="think-orb" aria-hidden="true" />
          <span className="think-label">{active ? active.label : 'Starting'}</span>
        </span>
      </div>

      <ol className="think-steps" aria-hidden="true">
        {STAGES.map((s, i) => {
          const state = i < current ? 'done' : i === current ? 'active' : 'todo';
          return (
            <li key={s.id} className={`think-step is-${state}`}>
              <span className="think-step-icon"><StepIcon id={s.id} /></span>
              <span className="think-step-label">{s.label}</span>
            </li>
          );
        })}
      </ol>

      <p className="think-detail">{detail}</p>

      {writing ? (
        paragraphs.map((p, i) => (
          <p
            className={`answer-body think-para${
              i === paragraphs.length - 1 ? ' think-caret' : ''
            }`}
            key={i}
          >
            {p}
          </p>
        ))
      ) : (
        <div className="think-skeleton" aria-hidden="true">
          <span style={{ width: '94%' }} />
          <span style={{ width: '99%' }} />
          <span style={{ width: '72%' }} />
          <span className="think-gap" />
          <span style={{ width: '88%' }} />
          <span style={{ width: '61%' }} />
        </div>
      )}

      {/* Announced once per step, not on every token. */}
      <p className="think-sr" role="status" aria-live="polite">
        {detail}
      </p>
    </div>
  );
}