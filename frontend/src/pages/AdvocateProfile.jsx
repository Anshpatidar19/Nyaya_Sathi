import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import {
  cancelConnection,
  fetchAdvocate,
  sendConnectionRequest,
} from '../networkApi';
import { NetworkPage, refreshBadges } from '../components/AppShell';
import {
  Avatar,
  Chips,
  ConnectButton,
  ConnectDialog,
  ConnectionBadge,
  DemoBadge,
  Disclaimer,
  locationOf,
} from '../components/NetworkBits';

export default function AdvocateProfile() {
  const { id } = useParams();
  const { token, isAdvocate, loading: authLoading } = useAuth();
  const navigate = useNavigate();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showDialog, setShowDialog] = useState(false);
  const [dialogError, setDialogError] = useState('');

  // An advocate reaching the directory by URL gets sent back rather than
  // shown a tool that isn't for them. Mirrors the guard on /matters, in the
  // other direction. `isAdvocate` is false while the profile is still
  // loading, so this waits for auth to settle first.
  useEffect(() => {
    if (!authLoading && isAdvocate) navigate('/network', { replace: true });
  }, [authLoading, isAdvocate, navigate]);

  useEffect(() => {
    if (authLoading || !token || isAdvocate) return;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, token, isAdvocate, id]);

  async function load() {
    setLoading(true);
    try {
      setData(await fetchAdvocate(token, id));
      setError('');
    } catch (err) {
      setError(err.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  async function doConnect(intro) {
    setBusy(true);
    setDialogError('');
    try {
      await sendConnectionRequest(token, {
        receiver_id: Number(id),
        intro_message: intro || null,
      });
      setShowDialog(false);
      await load();
      refreshBadges();
    } catch (err) {
      setDialogError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function doCancel() {
    const cid = data?.connection?.connection_id;
    if (!cid) return;
    setBusy(true);
    try {
      await cancelConnection(token, cid);
      await load();
      refreshBadges();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const user = data?.user;
  const p = data?.profile;

  return (
    <NetworkPage active="advocates">
      {showDialog && user && (
        <ConnectDialog
          advocate={user}
          busy={busy}
          error={dialogError}
          onClose={() => {
            setShowDialog(false);
            setDialogError('');
          }}
          onSend={doConnect}
        />
      )}

      <div className="page-scroll">
        <div className="page-wrap nx-narrow">
          <button
            type="button"
            className="nx-back"
            onClick={() => navigate('/advocates')}
          >
            &larr; Back to search
          </button>

          {loading ? (
            <div className="nx-profile-head nx-skel-head">
              <span className="skel-ic" />
              <span className="skel-body">
                <span className="skel-line w60" />
                <span className="skel-line w40" />
              </span>
            </div>
          ) : error ? (
            <div className="page-empty">
              <p>{error}</p>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => navigate('/advocates')}
              >
                Back to search
              </button>
            </div>
          ) : (
            <>
              <div className="nx-profile-head">
                <Avatar user={user} size={84} />
                <div className="nx-profile-ident">
                  <div className="nx-adv-name-row">
                    <h1>{user.name}</h1>
                    <DemoBadge user={user} />
                  </div>
                  <div className="nx-profile-role">
                    Advocate
                    {p?.specialization && (
                      <>
                        <span className="nx-dot">&middot;</span>
                        {p.specialization}
                      </>
                    )}
                  </div>
                  <div className="nx-profile-meta">
                    {locationOf(user, p?.practice_city)}
                    {p?.years_experience != null && (
                      <>
                        <span className="nx-dot">&middot;</span>
                        {p.years_experience} years of experience
                      </>
                    )}
                  </div>
                  {p?.current_firm && (
                    <div className="nx-profile-firm">{p.current_firm}</div>
                  )}
                </div>
                <div className="nx-profile-actions">
                  <ConnectionBadge connection={data.connection} />
                  <ConnectButton
                    connection={data.connection}
                    busy={busy}
                    onConnect={() => setShowDialog(true)}
                    onCancel={doCancel}
                    onMessage={() =>
                      navigate(
                        data.connection?.thread_id
                          ? `/messages/${data.connection.thread_id}`
                          : '/messages'
                      )
                    }
                  />
                </div>
              </div>

              <Disclaimer text={data.disclaimer} />

              {(p?.professional_bio || data.bio) && (
                <Section title="About">
                  <p className="nx-prose">{p?.professional_bio || data.bio}</p>
                </Section>
              )}

              {(p?.practice_areas || p?.courts || p?.languages) && (
                <Section title="Practice">
                  {p.practice_areas && (
                    <Field label="Areas of practice">
                      <Chips value={p.practice_areas} max={12} />
                    </Field>
                  )}
                  {p.courts && (
                    <Field label="Courts">
                      <Chips value={p.courts} max={12} />
                    </Field>
                  )}
                  {p.languages && (
                    <Field label="Languages">
                      <Chips value={p.languages} max={12} />
                    </Field>
                  )}
                </Section>
              )}

              {(p?.llb_college || p?.llm_college || p?.other_qualifications) && (
                <Section title="Education">
                  {p.llb_college && (
                    <Field label="LL.B.">
                      {p.llb_college}
                      {p.llb_year ? `, ${p.llb_year}` : ''}
                    </Field>
                  )}
                  {p.llm_college && <Field label="LL.M.">{p.llm_college}</Field>}
                  {p.other_qualifications && (
                    <Field label="Other qualifications">
                      {p.other_qualifications}
                    </Field>
                  )}
                </Section>
              )}

              {(p?.notable_experience || p?.previous_firms) && (
                <Section title="Experience">
                  {p.previous_firms && (
                    <Field label="Previously">{p.previous_firms}</Field>
                  )}
                  {p.notable_experience && (
                    <Field label="Notable work">{p.notable_experience}</Field>
                  )}
                </Section>
              )}

              {p?.bar_council_number && (
                <Section title="Enrolment">
                  <Field label="Bar Council number">
                    {p.bar_council_number}
                  </Field>
                  <p className="nx-fine">
                    Enrolment details are as entered by the advocate and are not
                    verified by Nyaya Sathi.
                  </p>
                </Section>
              )}

              {!p && (
                <div className="page-empty">
                  <p>
                    This advocate has not completed their professional profile
                    yet.
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </NetworkPage>
  );
}

function Section({ title, children }) {
  return (
    <section className="nx-section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function Field({ label, children }) {
  return (
    <div className="nx-field-row">
      <span className="nx-field-label">{label}</span>
      <div className="nx-field-value">{children}</div>
    </div>
  );
}