import { Link } from 'react-router-dom';

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
              <li><Link to="/#features">What it does</Link></li>
              <li><Link to="/#how">How it works</Link></li>
              <li><Link to="/ask">Ask a question</Link></li>
            </ul>
          </div>
          <div>
            <h5>Trust</h5>
            <ul>
              <li><Link to="/#trust">Trust &amp; safety</Link></li>
              <li><Link to="/#faq">FAQ</Link></li>
              <li><Link to="/#">Data &amp; privacy</Link></li>
            </ul>
          </div>
          <div>
            <h5>Company</h5>
            <ul>
              <li><Link to="/#">About</Link></li>
              <li><Link to="/#">Contact</Link></li>
              <li><Link to="/#">Careers</Link></li>
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