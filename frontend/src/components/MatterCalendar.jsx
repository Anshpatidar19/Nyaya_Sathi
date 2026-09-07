import { useMemo, useState } from 'react';

/* Month grid with a dot on any day that has something scheduled. Deliberately
   hand-rolled rather than pulled from a date library: it renders one month of
   read-mostly data, and a dependency would outweigh the ~40 lines of maths. */

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const DAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

const CHEV_L = (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 4l-5 6 5 6" />
  </svg>
);
const CHEV_R = (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 4l5 6-5 6" />
  </svg>
);

// Local ISO date. `toISOString()` converts to UTC first, which in IST shifts
// anything before 05:30 back a day — enough to put a hearing on the wrong date.
export function isoDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function formatDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTHS[m - 1]?.slice(0, 3)} ${y}`;
}

export default function MatterCalendar({ events = [], selected, onSelect }) {
  const today = new Date();
  const [cursor, setCursor] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));

  const byDate = useMemo(() => {
    const map = {};
    events.forEach((e) => {
      (map[e.event_date] = map[e.event_date] || []).push(e);
    });
    return map;
  }, [events]);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const firstWeekday = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const todayIso = isoDate(today);

  const cells = [];
  for (let i = 0; i < firstWeekday; i += 1) cells.push(null);
  for (let d = 1; d <= daysInMonth; d += 1) cells.push(d);

  function shift(by) {
    setCursor(new Date(year, month + by, 1));
  }

  return (
    <div className="cal">
      <div className="cal-head">
        <button type="button" className="cal-nav" onClick={() => shift(-1)} aria-label="Previous month">
          {CHEV_L}
        </button>
        <span className="cal-title">{MONTHS[month]} {year}</span>
        <button type="button" className="cal-nav" onClick={() => shift(1)} aria-label="Next month">
          {CHEV_R}
        </button>
      </div>

      <div className="cal-grid cal-dow">
        {DAYS.map((d) => <span key={d}>{d}</span>)}
      </div>

      <div className="cal-grid">
        {cells.map((d, i) => {
          if (d === null) return <span className="cal-cell empty" key={`e${i}`} />;
          const iso = isoDate(new Date(year, month, d));
          const has = !!byDate[iso];
          const classes = [
            'cal-cell',
            iso === todayIso ? 'today' : '',
            iso === selected ? 'selected' : '',
            has ? 'has-event' : '',
          ].filter(Boolean).join(' ');
          return (
            <button
              type="button"
              className={classes}
              key={iso}
              onClick={() => onSelect?.(iso === selected ? null : iso)}
              title={has ? byDate[iso].map((e) => e.title).join(', ') : undefined}
            >
              {d}
              {has && <span className="cal-dot" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}