import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../AuthContext';

const SAMPLE_ANSWERS = {
  rent: {
    q: 'Can my landlord evict me without any notice?',
    title: 'Not without proper notice',
    body: "No — your landlord must give written notice and follow the process set out in your state's rent law before you can be asked to leave. If there's no written agreement, the notice period is usually longer, not shorter.",
    cites: [
      ['State Rent Control Act', 'Eviction & notice provisions'],
      ['Transfer of Property Act, 1882', 'Section 106 · Tenancy at will'],
    ],
    steps: [
      'Ask your landlord for the eviction notice in writing.',
      'If none was given, reply in writing that you have not received valid notice.',
    ],
  },
  cheque: {
    q: 'My cheque bounced — what happens now?',
    title: 'You have 30 days to send a demand notice',
    body: "The payee must send a written demand notice within 30 days of the bounce, and the drawer then has 15 days to pay. If payment still doesn't come, a complaint can be filed within one month after that.",
    cites: [
      ['Negotiable Instruments Act, 1881', 'Section 138 · Dishonour of cheque'],
      ['Negotiable Instruments Act, 1881', 'Section 142 · Filing timeline'],
    ],
    steps: [
      'Send a written demand notice within 30 days of the bounce.',
      'Wait 15 days for payment before filing a complaint.',
    ],
  },
  job: {
    q: 'Can a company fire an employee without notice?',
    title: 'Not without notice or pay in lieu',
    body: "Not unless it's for proven misconduct — permanent employees are entitled to notice under standing orders, and any retrenchment also requires a month's notice and compensation.",
    cites: [
      ['Industrial Employment (Standing Orders) Act, 1946', 'Model standing orders'],
      ['Industrial Disputes Act, 1947', 'Section 25F · Retrenchment'],
    ],
    steps: [
      'Ask for the termination reason and notice period in writing.',
      'Check whether retrenchment compensation is due under Section 25F.',
    ],
  },
  consumer: {
    q: 'The product I bought is faulty — can I get a refund?',
    title: 'Yes, you can seek a refund or replacement',
    body: 'Yes — a defective product entitles you to a repair, replacement, or refund, and you can file a complaint even without a lawyer. E-commerce purchases carry the same protection.',
    cites: [
      ['Consumer Protection Act, 2019', 'Right to seek redress'],
      ['Consumer Protection (E-Commerce) Rules, 2020', 'Marketplace liability'],
    ],
    steps: [
      'Contact the seller in writing and keep proof of purchase.',
      'If unresolved, file a complaint on the e-Daakhil portal.',
    ],
  },
};

export default function Landing() {
  const [active, setActive] = useState('rent');
  const answer = SAMPLE_ANSWERS[active];

  // Signed-in state is read from `token`, not `user`. The token comes straight
  // out of localStorage in AuthProvider's useState initialiser, so it is known
  // on the very first render; `user` only arrives after fetchMe resolves.
  // Keying off `user` here would flash the signup buttons at an account that
  // is already logged in, every time the landing page loads.
  //
  // Same reason the hero copy uses `user?.name` with a fallback: the button is
  // correct immediately, the greeting fills in a moment later.
  const { token, user } = useAuth();
  const signedIn = Boolean(token);
  const firstName = user?.name?.split(' ')[0];

  return (
    <main>
      {/* HERO */}
      <section className="hero">
        <div className="wrap hero-grid">
          <div>
            <span className="eyebrow">
              <span className="dot" />
              {signedIn
                ? 'Live product — your questions stay private to your account'
                : 'Live product — sign up to ask your own question'}
            </span>
            <h1>Know your rights, <span className="accent-word">explained simply.</span></h1>
            <p className="hero-sub">Ask any legal question in your own language. Nyaya Sathi turns Indian law into plain-language answers and a clear next step — never the raw statute, never legal jargon.</p>
            <div className="hero-actions">
              {signedIn ? (
                <Link to="/ask" className="btn btn-primary">Ask a question →</Link>
              ) : (
                <>
                  <Link to="/register" className="btn btn-primary">Ask your question free →</Link>
                  <Link to="/login" className="btn btn-ghost">I already have an account</Link>
                </>
              )}
            </div>
            <div className="trust-row">
              <span className="avatars"><span /><span /><span /><span /></span>
              <span>Built for citizens across every Indian state · Hindi, English &amp; 4 more languages</span>
            </div>
          </div>

          <div className="demo-card">
            <div className="demo-topbar">
              <div className="demo-brand"><span className="sq">न्या</span> Research</div>
              <div className="status-chip"><span className="dot" /> Plain-language answer</div>
            </div>
            <div className="demo-question">{answer.q}</div>
            <h4 className="answer-title">{answer.title}</h4>
            <p className="answer-body">{answer.body}</p>
            <div className="cite-list">
              {answer.cites.map(([title, sub], i) => (
                <div className="cite" key={title}>
                  <span className="num">{i + 1}</span>
                  <div>
                    <div className="cite-title">{title}</div>
                    <div className="cite-sub">{sub}</div>
                  </div>
                </div>
              ))}
            </div>
            <div className="demo-steps">
              <div className="label">Suggested next step</div>
              {answer.steps.map((s, i) => (
                <div className="step-row" key={s}><span className="num">{i + 1}</span> {s}</div>
              ))}
            </div>
            <div className="sample-qs">
              {Object.entries(SAMPLE_ANSWERS).map(([key, a]) => (
                <button
                  key={key}
                  className={active === key ? 'active' : ''}
                  onClick={() => setActive(key)}
                >
                  {a.title.length > 26 ? a.q.slice(0, 24) + '…' : a.title}
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* LANGUAGE STRIP */}
      <div className="lang-strip">
        <div className="wrap">
          <span className="caption">Ask in the language you think in</span>
          <div className="lang-chips">
            <span>English</span><span>हिन्दी</span><span>मराठी</span><span>தமிழ்</span><span>తెలుగు</span><span>ಕನ್ನಡ</span>
          </div>
        </div>
      </div>

      {/* FEATURES */}
      <section id="features">
        <div className="wrap">
          <div className="section-head">
            <div className="kicker">What it does</div>
            <h2>Everything routed through one plain-language layer</h2>
            <p className="section-sub">Every feature is built around the same rule: the person asking never has to read a statute, a judgment, or a court filing to understand what's happening to them.</p>
          </div>
          <div className="feature-grid">
            <FeatureCard icon={<IconLanguages />} title="Ask in your own language" text="Type or speak in English, Hindi, or a regional language. The platform detects your state so answers reflect local amendments, not just central law." />
            <FeatureCard icon={<IconScanText />} title="Read your notice for you" text="Photograph a court notice, police summons, or rent agreement. It's read, explained in simple terms, and any deadline is called out clearly." />
            <FeatureCard icon={<IconChecklist />} title="Tells you what to do next" text='Not dense analysis — a short, ordered checklist. "Send a notice within 15 days" instead of a paragraph explaining why.' />
            <FeatureCard icon={<IconShieldCheck />} title="Redacts before it reads" text="Names, phone numbers, and ID numbers in an uploaded document are removed automatically before anything reaches the AI model." />
            <FeatureCard icon={<IconFilePen />} title="Drafts the basic paperwork" text="Consumer complaints, RTI applications, and standard notices, generated in the right format and filled in with your details." />
            <FeatureCard icon={<IconScale />} title="Names the law, not the file" text='Answers cite the broad act — "the Consumer Protection Act, 2019" — never an internal document ID or a direct quote from the source text.' />
            <FeatureCard icon={<IconUserSearch />} title="Finds your advocate, matched" text="Describe your situation and get a ranked shortlist by practice area, location, and language — with the reason behind every match shown, not just a score." />
            <FeatureCard icon={<IconGavel />} title="Builds the case for your advocate" text="For advocates: give a matter's facts and get grounded primary arguments, the likely opposing case, and rebuttals — cited to the same statutes and judgments, never invented." />
            <FeatureCard icon={<IconCalendarClock />} title="Tracks every matter in one place" text="Case files, hearing dates, notes, and research stay together per matter, with a calendar view so no deadline gets missed." />
            <FeatureCard icon={<IconMessageCircle />} title="Connect and message advocates directly" text="Send a connection request, then chat securely once it's accepted — no phone number exchanged, nothing lost across channels." />
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how">
        <div className="wrap">
          <div className="section-head">
            <div className="kicker">How it works</div>
            <h2>From a photo or a question to a next step</h2>
            <p className="section-sub">Retrieval runs against real Indian case law and statutes, but you only ever see the plain-language result.</p>
          </div>
          <div className="flow">
            <FlowStep n={1} title="Ask or upload" text="Type a question, speak it, or upload a photo of a notice — in any supported language." />
            <FlowStep n={2} title="Understood & localised" text="The query is clarified and matched to your state, so state-specific law is used, not just the central act." />
            <FlowStep n={3} title="Checked against real judgments" text="Retrieval runs against indexed case law and statutes, and a validator checks the answer is actually grounded in it." />
            <FlowStep n={4} title="Plain answer, clear step" text="You receive a short explanation and an ordered checklist — never raw legal text." />
          </div>
        </div>
      </section>

      {/* TRUST STRIP */}
      <section id="trust">
        <div className="wrap">
          <div className="trust-strip">
            <div className="trust-strip-grid">
              <div>
                <h2>Built to inform — not to practise law</h2>
                <p>Every answer is checked against the retrieved source before it reaches you, and every response is information, not legal advice, in line with the Advocates Act, 1961.</p>
              </div>
              <div className="stat"><div className="num">100%</div><div className="lbl">of answers checked by a second validator agent</div></div>
              <div className="stat"><div className="num">0</div><div className="lbl">raw source files or database IDs shown to users</div></div>
              <div className="stat"><div className="num">DPDP</div><div className="lbl">Act, 2023 aligned data handling</div></div>
            </div>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq">
        <div className="wrap">
          <div className="section-head">
            <div className="kicker">FAQ</div>
            <h2>Questions this demo should answer</h2>
          </div>
          <div className="faq">
            <FaqItem q="Is this legal advice?" defaultOpen>
              No. Nyaya Sathi gives legal information in plain language, grounded in Indian statutes and rules. It doesn't replace advice from a qualified advocate, and every screen says so clearly.
            </FaqItem>
            <FaqItem q="Why don't answers show the exact section or case?">
              The platform is deliberately "blind-to-user" — it cites the broad act so answers stay credible and simple, while the underlying database of statutes and case law stays hidden from the response itself.
            </FaqItem>
            <FaqItem q="What happens to a photo I upload?">
              Personally identifiable information is redacted before the text reaches the language model, and the uploaded image is deleted automatically once the text has been extracted.
            </FaqItem>
            <FaqItem q="Which languages are supported?">
              English and Hindi at launch, with Marathi, Tamil, Telugu, and Kannada rolling out alongside state-specific legal content.
            </FaqItem>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section>
        <div className="wrap">
          <div className="cta-band">
            {signedIn ? (
              <>
                <h2>{firstName ? `Ask your next question, ${firstName}` : 'Ask your next question'}</h2>
                <p>Your questions and history stay private to your account.</p>
                <Link to="/ask" className="btn btn-primary">Ask a question →</Link>
              </>
            ) : (
              <>
                <h2>Create a free account and ask your question</h2>
                <p>Sign up in under a minute — your questions and history stay private to your account.</p>
                <Link to="/register" className="btn btn-primary">Get started free →</Link>
              </>
            )}
            <div className="disclaimer-line">Nyaya Sathi provides legal information, not legal advice, and does not create an advocate–client relationship.</div>
          </div>
        </div>
      </section>
    </main>
  );
}

// Feature icons. Hand-written stroke SVGs rather than emoji: emoji render as
// a different colourful glyph on every OS, which is the one thing that makes
// an otherwise clean grid look unfinished. These inherit the accent colour, so
// they follow the dark theme without a second set of assets, and they need no
// icon package added to the dependency list.
//
// Shared wrapper keeps every icon on the same grid, weight, and cap style -
// mixing stroke widths across six cards is what usually reads as "off".
function Icon({ children }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="21"
      height="21"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      style={{ color: 'var(--accent)' }}
    >
      {children}
    </svg>
  );
}

function IconLanguages() {
  return (
    <Icon>
      <path d="m5 8 6 6" />
      <path d="m4 14 6-6 2-3" />
      <path d="M2 5h12" />
      <path d="M7 2h1" />
      <path d="m22 22-5-10-5 10" />
      <path d="M14 18h6" />
    </Icon>
  );
}

function IconScanText() {
  return (
    <Icon>
      <path d="M3 7V5a2 2 0 0 1 2-2h2" />
      <path d="M17 3h2a2 2 0 0 1 2 2v2" />
      <path d="M21 17v2a2 2 0 0 1-2 2h-2" />
      <path d="M7 21H5a2 2 0 0 1-2-2v-2" />
      <path d="M7 8h10" />
      <path d="M7 12h10" />
      <path d="M7 16h6" />
    </Icon>
  );
}

function IconChecklist() {
  return (
    <Icon>
      <path d="m3 7 2 2 4-4" />
      <path d="m3 17 2 2 4-4" />
      <path d="M13 6h8" />
      <path d="M13 12h8" />
      <path d="M13 18h8" />
    </Icon>
  );
}

function IconShieldCheck() {
  return (
    <Icon>
      <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
      <path d="m9 12 2 2 4-4" />
    </Icon>
  );
}

function IconFilePen() {
  return (
    <Icon>
      <path d="M12.5 22H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8.5L20 7.5v3.5" />
      <path d="M14 2v6h6" />
      <path d="M13.4 15.6a1 1 0 1 0-3-3l-5 5a2 2 0 0 0-.5.86l-.84 2.87a.5.5 0 0 0 .62.62l2.87-.84a2 2 0 0 0 .85-.5z" />
    </Icon>
  );
}

function IconScale() {
  return (
    <Icon>
      <path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1" />
      <path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1" />
      <path d="M7 21h10" />
      <path d="M12 3v18" />
      <path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2" />
    </Icon>
  );
}

function IconUserSearch() {
  return (
    <Icon>
      <circle cx="10" cy="8" r="5" />
      <path d="M2 21a8 8 0 0 1 10.434-7.62" />
      <circle cx="17" cy="17" r="3" />
      <path d="m21 21-1.9-1.9" />
    </Icon>
  );
}

function IconGavel() {
  return (
    <Icon>
      <path d="m14.5 12.5-8 8a2.119 2.119 0 1 1-3-3l8-8" />
      <path d="m16 16 6-6" />
      <path d="m8 8 6-6" />
      <path d="m9 7 8 8" />
      <path d="m21 11-8-8" />
    </Icon>
  );
}

function IconCalendarClock() {
  return (
    <Icon>
      <path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5" />
      <path d="M16 2v4" />
      <path d="M8 2v4" />
      <path d="M3 10h18" />
      <circle cx="18" cy="18" r="4" />
      <path d="M18 16.5v1.5l1 1" />
    </Icon>
  );
}

function IconMessageCircle() {
  return (
    <Icon>
      <path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z" />
      <path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1" />
    </Icon>
  );
}

function FeatureCard({ icon, title, text }) {
  return (
    <div className="feature-card">
      <div className="feature-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}

function FlowStep({ n, title, text }) {
  return (
    <div className="flow-step">
      <div className="flow-num">{n}</div>
      <h4>{title}</h4>
      <p>{text}</p>
    </div>
  );
}

function FaqItem({ q, children, defaultOpen }) {
  return (
    <details className="faq-item" open={defaultOpen}>
      <summary>{q} <span className="plus">+</span></summary>
      <div className="faq-a">{children}</div>
    </details>
  );
}