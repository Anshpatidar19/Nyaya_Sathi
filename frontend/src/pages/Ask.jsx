import { useEffect, useState } from 'react';
import { useAuth } from '../AuthContext';
import { askQuestion, fetchHistory } from '../api';

export default function Ask() {
  const { token, user } = useAuth();
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchHistory(token).then(setHistory).catch(() => {});
  }, [token]);

  async function handleAsk(e) {
    e.preventDefault();
    if (!question.trim()) return;
    setError('');
    setLoading(true);
    try {
      const data = await askQuestion(token, { question, state: user?.state });
      setAnswer({ ...data, question });
      const freshHistory = await fetchHistory(token);
      setHistory(freshHistory);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="ask-shell">
      <div className="wrap ask-grid">
        <aside className="ask-sidebar">
          <h4>Your recent questions</h4>
          {history.length === 0 && <div className="history-empty">Nothing asked yet — try a question on the right.</div>}
          {history.map((h) => (
            <div className="history-item" key={h.id} onClick={() => setQuestion(h.question)}>
              {h.question}
            </div>
          ))}
        </aside>

        <div>
          <form className="ask-form" onSubmit={handleAsk}>
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={`Ask a legal question${user?.state ? ` (using ${user.state} law where relevant)` : ''}…`}
            />
            <button className="btn btn-primary" type="submit" disabled={loading}>
              {loading ? <span className="spinner" /> : 'Ask'}
            </button>
          </form>

          {error && <div className="form-error">{error}</div>}

          {!answer && !loading && (
            <div className="ask-placeholder">
              Ask something like "Can my landlord evict me without notice?" or "My cheque bounced, what do I do?"
            </div>
          )}

          {answer && (
            <div className="demo-card">
              <div className="demo-topbar">
                <div className="demo-brand"><span className="sq">न्या</span> Research</div>
                <div className="status-chip live"><span className="dot" /> Answered live</div>
              </div>
              <div className="demo-question">{answer.question}</div>
              <h4 className="answer-title">{answer.title}</h4>
              <p className="answer-body">{answer.body}</p>

              {answer.citations?.length > 0 && (
                <div className="cite-list">
                  {answer.citations.map((c, i) => (
                    <div className="cite" key={i}>
                      <span className="num">{i + 1}</span>
                      <div>
                        <div className="cite-title">{c.title}</div>
                        <div className="cite-sub">{c.source}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {answer.next_steps?.length > 0 && (
                <div className="demo-steps">
                  <div className="label">Suggested next step</div>
                  {answer.next_steps.map((s, i) => (
                    <div className="step-row" key={i}><span className="num">{i + 1}</span> {s}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
