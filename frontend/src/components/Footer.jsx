export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="wrap">
        <div className="footer-grid">
          <div className="footer-brand">
            <div className="logo"><span className="mark">न्या</span> Nyaya Sathi</div>
            <p>Legal information for every Indian, in plain language — grounded in Indian law, never in jargon.</p>
          </div>
          <div>
            <h5>Product</h5>
            <ul>
              <li><a href="/#features">What it does</a></li>
              <li><a href="/#how">How it works</a></li>
              <li><a href="/ask">Ask a question</a></li>
            </ul>
          </div>
          <div>
            <h5>Trust</h5>
            <ul>
              <li><a href="/#trust">Trust &amp; safety</a></li>
              <li><a href="/#faq">FAQ</a></li>
              <li><a href="/#">Data &amp; privacy</a></li>
            </ul>
          </div>
          <div>
            <h5>Company</h5>
            <ul>
              <li><a href="/#">About</a></li>
              <li><a href="/#">Contact</a></li>
              <li><a href="/#">Careers</a></li>
            </ul>
          </div>
        </div>
        <div className="footer-bottom">
          <span>Legal information, not legal advice · does not create an advocate–client relationship</span>
          <span>© 2026 Nyaya Sathi</span>
        </div>
      </div>
    </footer>
  );
}
