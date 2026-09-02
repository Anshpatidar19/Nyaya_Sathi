import { useState } from 'react';
import { Link } from 'react-router-dom';

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

  return (
    <main>
      {/* HERO */}
      <section className="hero">
        <div className="wrap hero-grid">
          <div>
            <span className="eyebrow"><span className="dot" /> Live product — sign up to ask your own question</span>
            <h1>Know your rights, <span className="accent-word">explained simply.</span></h1>
            <p className="hero-sub">Ask any legal question in your own language. Nyaya Sathi turns Indian law into plain-language answers and a clear next step — never the raw statute, never legal jargon.</p>
            <div className="hero-actions">
              <Link to="/register" className="btn btn-primary">Ask your question free →</Link>
              <Link to="/login" className="btn btn-ghost">I already have an account</Link>
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
            <FeatureCard icon="🗣️" title="Ask in your own language" text="Type or speak in English, Hindi, or a regional language. The platform detects your state so answers reflect local amendments, not just central law." />
            <FeatureCard icon="📄" title="Read your notice for you" text="Photograph a court notice, police summons, or rent agreement. It's read, explained in simple terms, and any deadline is called out clearly." />
            <FeatureCard icon="✅" title="Tells you what to do next" text='Not dense analysis — a short, ordered checklist. "Send a notice within 15 days" instead of a paragraph explaining why.' />
            <FeatureCard icon="🛡️" title="Redacts before it reads" text="Names, phone numbers, and ID numbers in an uploaded document are removed automatically before anything reaches the AI model." />
            <FeatureCard icon="📝" title="Drafts the basic paperwork" text="Consumer complaints, RTI applications, and standard notices, generated in the right format and filled in with your details." />
            <FeatureCard icon="⚖️" title="Names the law, not the file" text='Answers cite the broad act — "the Consumer Protection Act, 2019" — never an internal document ID or a direct quote from the source text.' />
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
            <h2>Create a free account and ask your question</h2>
            <p>Sign up in under a minute — your questions and history stay private to your account.</p>
            <Link to="/register" className="btn btn-primary">Get started free →</Link>
            <div className="disclaimer-line">Nyaya Sathi provides legal information, not legal advice, and does not create an advocate–client relationship.</div>
          </div>
        </div>
      </section>
    </main>
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
