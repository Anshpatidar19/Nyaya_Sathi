import { useState } from 'react';

/* Sections collapse because the full analysis is long. Everything an advocate
   needs first — the arguments themselves — is open by default; the supporting
   material starts closed so the page opens on something readable. */

const CHEVRON = (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 8l4 4 4-4" />
  </svg>
);

function Section({ title, count, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  if (!children) return null;
  return (
    <section className={`arg-section ${open ? 'open' : ''}`}>
      <button type="button" className="arg-section-head" onClick={() => setOpen((o) => !o)}>
        <span className="arg-section-title">{title}</span>
        {count != null && <span className="arg-count">{count}</span>}
        <span className="arg-chevron">{CHEVRON}</span>
      </button>
      {open && <div className="arg-section-body">{children}</div>}
    </section>
  );
}

function Authorities({ items, inline = false }) {
  if (!items?.length) return null;
  return (
    <div className={`arg-auth-list ${inline ? 'inline' : ''}`}>
      {items.map((a, i) => {
        const href = a.url || (a.docid ? `https://indiankanoon.org/doc/${a.docid}/` : null);
        const inner = (
          <>
            <span className="arg-auth-title">{a.title}</span>
            <span className="arg-auth-source">{a.source}</span>
          </>
        );
        return href ? (
          <a className="arg-auth arg-auth-link" key={i} href={href} target="_blank" rel="noopener noreferrer">
            {inner}
            <span className="arg-auth-go" aria-hidden="true">&#8599;</span>
          </a>
        ) : (
          <span className="arg-auth" key={i}>{inner}</span>
        );
      })}
    </div>
  );
}

function Bullets({ items, className = '' }) {
  if (!items?.length) return null;
  return (
    <ul className={`arg-bullets ${className}`}>
      {items.map((t, i) => <li key={i}>{t}</li>)}
    </ul>
  );
}

const STRENGTH_KEY = (s) => (s || 'moderate').replace(/\s+/g, '-');

export default function ArgumentsResult({ data, onCopy, copied }) {
  const ov = data.case_overview || {};
  const facts = data.facts || {};

  return (
    <div className="demo-card arg-card">
      <div className="demo-topbar">
        <div className="demo-brand"><span className="sq">न्या</span> Arguments</div>
        <div className="arg-top-right">
          <span className="arg-side-chip">For the {data.side}</span>
          <button type="button" className="copy-btn" onClick={onCopy}>
            {copied ? 'Copied' : 'Copy all'}
          </button>
        </div>
      </div>

      <h4 className="answer-title">{data.title}</h4>

      {data.document_name && (
        <p className="arg-source-note">Built from {data.document_name}</p>
      )}
      {data.truncated && (
        <div className="advocate-warning">
          The material was long, so only the first part was analysed.
        </div>
      )}

      {/* ---- Case overview ---- */}
      <Section title="Case overview" defaultOpen>
        <div className="arg-overview-wrap">
        <dl className="arg-overview">
          {ov.parties && (<><dt>Parties</dt><dd>{ov.parties}</dd></>)}
          {ov.cause_of_action && (<><dt>Cause of action</dt><dd>{ov.cause_of_action}</dd></>)}
          {ov.stage && (<><dt>Stage</dt><dd>{ov.stage}</dd></>)}
          {ov.relief_sought && (<><dt>Relief sought</dt><dd>{ov.relief_sought}</dd></>)}
          {ov.material_facts && (<><dt>Material facts</dt><dd>{ov.material_facts}</dd></>)}
        </dl>

        {(facts.supporting?.length || facts.opposing?.length || facts.disputed?.length) > 0 && (
          <div className="arg-fact-grid">
            {facts.supporting?.length > 0 && (
              <div className="arg-fact-col for">
                <div className="label">Helps your side</div>
                <Bullets items={facts.supporting} />
              </div>
            )}
            {facts.opposing?.length > 0 && (
              <div className="arg-fact-col against">
                <div className="label">Helps the other side</div>
                <Bullets items={facts.opposing} />
              </div>
            )}
            {facts.disputed?.length > 0 && (
              <div className="arg-fact-col disputed">
                <div className="label">In dispute</div>
                <Bullets items={facts.disputed} />
              </div>
            )}
          </div>
        )}
        </div>
      </Section>

      {/* ---- Issues ---- */}
      {data.issues?.length > 0 && (
        <Section title="Issues for determination" count={data.issues.length}>
          <div className="arg-grid">
          {data.issues.map((iss, i) => (
            <div className="arg-block" key={i}>
              <div className="arg-block-head">
                <span className="arg-num">{i + 1}</span>
                <span className="arg-block-title">{iss.question}</span>
                <span className={`arg-pill imp-${iss.importance}`}>{iss.importance}</span>
              </div>
              {iss.elements?.length > 0 && (
                <>
                  <div className="arg-label">Must be established</div>
                  <Bullets items={iss.elements} />
                </>
              )}
              {iss.burden && <p className="arg-para"><strong>Burden:</strong> {iss.burden}</p>}
              <Authorities items={iss.authorities} />
            </div>
          ))}
          </div>
        </Section>
      )}

      {/* ---- Primary arguments ---- */}
      {data.arguments?.length > 0 && (
        <Section title="Your arguments" count={data.arguments.length} defaultOpen>
          <div className="arg-grid">
          {data.arguments.map((a, i) => (
            <div className="arg-block" key={i}>
              <div className="arg-block-head">
                <span className="arg-num">{i + 1}</span>
                <span className="arg-block-title">{a.title}</span>
                <span className={`arg-pill str-${STRENGTH_KEY(a.strength)}`}>{a.strength}</span>
              </div>
              {a.proposition && <p className="arg-para arg-lead">{a.proposition}</p>}
              {a.legal_basis && (
                <><div className="arg-label">Legal basis</div><p className="arg-para">{a.legal_basis}</p></>
              )}
              {a.application && (
                <><div className="arg-label">Application to these facts</div><p className="arg-para">{a.application}</p></>
              )}
              {a.strength_reason && (
                <p className="arg-note">Rated {a.strength}: {a.strength_reason}</p>
              )}
              <Authorities items={a.authorities} />
            </div>
          ))}
          </div>
        </Section>
      )}

      {/* ---- Alternatives ---- */}
      {data.alternative_arguments?.length > 0 && (
        <Section title="Alternative arguments" count={data.alternative_arguments.length}>
          <div className="arg-grid-2">
          {data.alternative_arguments.map((a, i) => (
            <div className="arg-block" key={i}>
              <div className="arg-block-head">
                <span className="arg-num">{i + 1}</span>
                <span className="arg-block-title">{a.title}</span>
                <span className="arg-pill kind">{a.kind}</span>
              </div>
              {a.proposition && <p className="arg-para arg-lead">{a.proposition}</p>}
              {a.legal_basis && <p className="arg-para">{a.legal_basis}</p>}
              <Authorities items={a.authorities} />
            </div>
          ))}
          </div>
        </Section>
      )}

      {/* ---- Opposing case ---- */}
      {data.opposing_arguments?.length > 0 && (
        <Section title="What the other side will argue" count={data.opposing_arguments.length} defaultOpen>
          <div className="arg-facing">
          {data.opposing_arguments.map((a, i) => (
            <div className="arg-block opposing" key={i}>
              <div className="arg-block-head">
                <span className="arg-num">{i + 1}</span>
                <span className="arg-block-title">{a.title}</span>
                <span className={`arg-pill str-${STRENGTH_KEY(a.strength)}`}>{a.strength}</span>
              </div>
              {a.position && <p className="arg-para arg-lead">{a.position}</p>}
              {a.legal_basis && <p className="arg-para">{a.legal_basis}</p>}
              <Authorities items={a.authorities} />
            </div>
          ))}
          </div>
        </Section>
      )}

      {/* ---- Rebuttals ---- */}
      {data.rebuttals?.length > 0 && (
        <Section title="Your rebuttals" count={data.rebuttals.length} defaultOpen>
          <div className="arg-facing">
          {data.rebuttals.map((r, i) => (
            <div className="arg-block rebuttal" key={i}>
              <div className="arg-block-head">
                <span className="arg-num">{i + 1}</span>
                <span className="arg-block-title">{r.opposing_argument}</span>
                {r.basis && <span className="arg-pill kind">{r.basis}</span>}
              </div>
              <p className="arg-para">{r.response}</p>
              <Authorities items={r.authorities} />
            </div>
          ))}
          </div>
        </Section>
      )}

      {/* ---- Strengths & weaknesses ---- */}
      {(data.strengths?.length > 0 || data.weaknesses?.length > 0) && (
        <Section title="Strengths and weaknesses" count={(data.strengths?.length || 0) + (data.weaknesses?.length || 0)}>
          <div className="arg-split">
            {data.strengths?.length > 0 && (
              <div className="arg-split-col">
                <div className="arg-label">Strengths</div>
                <Bullets items={data.strengths} className="good" />
              </div>
            )}
            {data.weaknesses?.length > 0 && (
              <div className="arg-split-col">
                <div className="arg-label">Weaknesses — assume the other side has spotted these</div>
                <div className="arg-grid-2">
                  {data.weaknesses.map((w, i) => (
                    <div className="arg-weakness" key={i}>
                      <div className="arg-weakness-title">{w.issue}</div>
                      {w.risk && <p className="arg-para"><strong>Risk:</strong> {w.risk}</p>}
                      {w.mitigation && <p className="arg-para"><strong>Mitigation:</strong> {w.mitigation}</p>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Section>
      )}

      {/* ---- Evidence ---- */}
      {data.evidence?.length > 0 && (
        <Section title="Evidence" count={data.evidence.length}>
          <div className="arg-evidence">
            {data.evidence.map((e, i) => (
              <div className={`arg-ev-row ${e.status}`} key={i}>
                <span className={`arg-pill ev-${e.status}`}>{e.status}</span>
                <span className="arg-ev-item">{e.item}</span>
                <span className="arg-ev-meta">{e.category} · {e.importance}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ---- Questions ---- */}
      {data.judicial_questions?.length > 0 && (
        <Section title="Questions the bench may ask" count={data.judicial_questions.length}>
          <div className="arg-grid-2">
            {data.judicial_questions.map((q, i) => (
              <div className="arg-qa" key={i}>
                <div className="arg-q">{q.question}</div>
                <p className="arg-para">{q.answer}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {data.opposing_questions?.length > 0 && (
        <Section title="Questions from opposing counsel" count={data.opposing_questions.length}>
          <div className="arg-grid-2">
            {data.opposing_questions.map((q, i) => (
              <div className="arg-qa" key={i}>
                <div className="arg-q">{q.question}</div>
                <p className="arg-para">{q.response}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ---- Strategy ---- */}
      {data.strategy?.length > 0 && (
        <Section title="Recommended strategy" count={data.strategy.length} defaultOpen>
          <ul className="arg-strategy-grid">
            {data.strategy.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </Section>
      )}

      {/* ---- Gaps ---- */}
      {(data.assumptions?.length > 0 || data.missing_information?.length > 0) && (
        <Section
          title="Assumptions and missing information"
          count={(data.assumptions?.length || 0) + (data.missing_information?.length || 0)}
          defaultOpen
        >
          <div className="arg-split">
            {data.assumptions?.length > 0 && (
              <div className="arg-split-col">
                <div className="arg-label">Assumed — verify before you rely on any of it</div>
                <Bullets items={data.assumptions} className="warn" />
              </div>
            )}
            {data.missing_information?.length > 0 && (
              <div className="arg-split-col">
                <div className="arg-label">Get this before filing</div>
                <Bullets items={data.missing_information} className="warn" />
              </div>
            )}
          </div>
        </Section>
      )}

      {/* ---- All sources ---- */}
      {data.sources?.length > 0 && (
        <Section title="All retrieved sources" count={data.sources.length}>
          <Authorities items={data.sources} inline />
        </Section>
      )}

      <p className="answer-disclaimer">
        Research assistance for an advocate, not settled opinion. Every authority
        above is retrieved from a source — open and read it before you cite it.
        Nothing here predicts how a court will decide.
      </p>
    </div>
  );
}