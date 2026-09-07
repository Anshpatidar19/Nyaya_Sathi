import { useState } from 'react';

/* Two ways in, like the reference: bring the pleading, or describe the matter.
   Either way the advocate must pick a side — an argument set with no party to
   argue for is just a case summary. */

const UPLOAD_IC = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 16V4M8 8l4-4 4 4" />
    <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
  </svg>
);

const TYPE_IC = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 20h4l10-10a2.5 2.5 0 0 0-3.5-3.5L4.5 16.5z" />
    <path d="M13.5 6.5l4 4" />
  </svg>
);

export default function ArgumentsSetup({
  sides,
  side,
  onSideChange,
  issue,
  onIssueChange,
  court,
  onCourtChange,
  attachment,
  uploading,
  onPickFile,
  onClearFile,
  onClose,
}) {
  const [path, setPath] = useState(attachment ? 'upload' : null);

  return (
    <div className="arg-setup-backdrop" role="dialog" aria-modal="true" aria-label="Generate arguments">
      <div className="arg-setup">
        <button type="button" className="arg-setup-x" onClick={onClose} aria-label="Close">×</button>

        <h2>Generate arguments and counter-arguments</h2>
        <p className="arg-setup-sub">
          Bring the case papers or describe the matter. You&apos;ll get arguments for
          your side, the case against, and how to answer it.
        </p>

        <div className="arg-path-grid">
          <button
            type="button"
            className={`arg-path ${path === 'upload' ? 'active' : ''}`}
            onClick={() => setPath('upload')}
          >
            <span className="arg-path-ic">{UPLOAD_IC}</span>
            <span className="arg-path-title">Upload case documents</span>
            <span className="arg-path-sub">
              Pleadings, notices, orders or judgments already on the file
            </span>
          </button>

          <button
            type="button"
            className={`arg-path ${path === 'type' ? 'active' : ''}`}
            onClick={() => setPath('type')}
          >
            <span className="arg-path-ic">{TYPE_IC}</span>
            <span className="arg-path-title">Type the facts</span>
            <span className="arg-path-sub">
              Start fresh with the facts and details of the matter
            </span>
          </button>
        </div>

        {path === 'upload' && (
          <div className="arg-drop">
            {attachment ? (
              <div className="arg-drop-done">
                <span className="arg-drop-name">{attachment.filename}</span>
                <button type="button" className="link-btn" onClick={onClearFile}>
                  Choose a different file
                </button>
              </div>
            ) : (
              <>
                <span className="arg-drop-ic">{UPLOAD_IC}</span>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={onPickFile}
                  disabled={uploading}
                >
                  {uploading ? <span className="spinner spinner-dark" /> : 'Browse file'}
                </button>
                <span className="arg-drop-hint">PDF, Word or text · up to 10 MB</span>
              </>
            )}
          </div>
        )}

        {path && (
          <>
            <div className="arg-setup-row">
              <div className="field">
                <label htmlFor="arg-side">You appear for</label>
                <select id="arg-side" value={side} onChange={(e) => onSideChange(e.target.value)}>
                  {sides.map((s) => (
                    <option value={s.id} key={s.id}>{s.label}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="arg-court">Court or forum (optional)</label>
                <input
                  id="arg-court"
                  value={court}
                  onChange={(e) => onCourtChange(e.target.value)}
                  placeholder="e.g. MP High Court, Jabalpur"
                />
              </div>
            </div>

            <div className="field">
              <label htmlFor="arg-issue">The legal issue (optional, but sharpens the research)</label>
              <input
                id="arg-issue"
                value={issue}
                onChange={(e) => onIssueChange(e.target.value)}
                placeholder="e.g. whether anticipatory bail lies where the offence is non-bailable"
              />
            </div>

            <button type="button" className="btn btn-primary btn-block" onClick={onClose}>
              {path === 'upload' && attachment
                ? 'Continue — add any notes, then generate'
                : 'Continue'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}