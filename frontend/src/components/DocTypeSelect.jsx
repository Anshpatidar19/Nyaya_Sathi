import { useEffect, useRef, useState } from 'react';

/* A native <select> decides for itself which way to open, and near the bottom
   of the viewport it flips upward and covers the conversation. This one
   defaults to opening downward, with the composer-dock placement (the only
   place it's actually used) overridden in CSS to open upward instead - see
   .dock-box .dts-menu in styles.css - since that placement always sits at
   the bottom of the screen and downward would put the menu off-screen. */

const CHEVRON = (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 8l4 4 4-4" />
  </svg>
);

export default function DocTypeSelect({
  value,
  onChange,
  groups,          // { "Agreement": [{id, name, needs_advocate}], ... }
  placeholder = 'Document type',
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const all = Object.values(groups || {}).flat();
  const selected = all.find((t) => t.id === value);

  function pick(id) {
    onChange(id);
    setOpen(false);
  }

  return (
    <div className={`dts ${open ? 'open' : ''}`} ref={rootRef}>
      <button
        type="button"
        className={`dts-trigger ${selected ? 'has-value' : ''}`}
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="dts-label">{selected ? selected.name : placeholder}</span>
        <span className="dts-chev">{CHEVRON}</span>
      </button>

      {open && (
        <div className="dts-menu" role="listbox">
          <button
            type="button"
            className={`dts-opt ${!value ? 'active' : ''}`}
            onClick={() => pick('')}
          >
            {placeholder}
          </button>

          {Object.keys(groups || {}).sort().map((cat) => (
            <div className="dts-group" key={cat}>
              <div className="dts-group-label">{cat}</div>
              {groups[cat].map((t) => (
                <button
                  type="button"
                  key={t.id}
                  role="option"
                  aria-selected={t.id === value}
                  className={`dts-opt ${t.id === value ? 'active' : ''}`}
                  onClick={() => pick(t.id)}
                >
                  {t.name}
                  {t.needs_advocate && (
                    <span className="dts-flag">needs advocate review</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}