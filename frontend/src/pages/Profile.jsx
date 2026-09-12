import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../AuthContext';
import {
  deleteAvatar,
  fetchMyProfile,
  saveAdvocateProfile,
  updateMyProfile,
  uploadAvatar,
} from '../networkApi';
import { NetworkPage, setCachedProfile } from '../components/AppShell';
import { Avatar, DemoBadge, Skeleton } from '../components/NetworkBits';

const MAX_AVATAR_MB = 2;
const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

const ADVOCATE_FIELDS = [
  { key: 'specialization', label: 'Primary practice area', placeholder: 'Criminal Law' },
  { key: 'years_experience', label: 'Years of experience', type: 'number', min: 0, max: 70 },
  { key: 'current_firm', label: 'Current firm or chamber', placeholder: 'Verma & Associates' },
  { key: 'practice_city', label: 'City where you practise', placeholder: 'Indore' },
  { key: 'bar_council_number', label: 'Bar Council enrolment number', placeholder: 'MP/2841/2010' },
  { key: 'practice_areas', label: 'Areas of practice', hint: 'Comma separated', placeholder: 'Criminal Law, Bail Matters, Cheque Bounce' },
  { key: 'courts', label: 'Courts you appear in', hint: 'Comma separated', placeholder: 'District Court Indore, MP High Court' },
  { key: 'languages', label: 'Languages', hint: 'Comma separated', placeholder: 'Hindi, English' },
  { key: 'llb_college', label: 'LL.B. college or university', placeholder: 'Devi Ahilya Vishwavidyalaya, Indore' },
  { key: 'llb_year', label: 'LL.B. graduation year', type: 'number', min: 1900, max: 2100 },
  { key: 'llm_college', label: 'LL.M. (if any)' },
  { key: 'other_qualifications', label: 'Other qualifications' },
];

export default function Profile() {
  const { token, isAdvocate, loading: authLoading } = useAuth();

  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [basic, setBasic] = useState({ name: '', city: '', state: '', bio: '' });
  const [adv, setAdv] = useState({});
  const [savingBasic, setSavingBasic] = useState(false);
  const [savingAdv, setSavingAdv] = useState(false);
  const [notice, setNotice] = useState('');
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarError, setAvatarError] = useState('');
  const fileRef = useRef(null);

  useEffect(() => {
    if (authLoading || !token) return;
    (async () => {
      try {
        const p = await fetchMyProfile(token);
        apply(p);
        setError('');
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, token]);

  function apply(p) {
    setProfile(p);
    setCachedProfile(p);
    setBasic({
      name: p.name || '',
      city: p.city || '',
      state: p.state || '',
      bio: p.bio || '',
    });
    const ap = p.advocate_profile || {};
    const next = { is_listed: ap.is_listed !== false };
    ADVOCATE_FIELDS.forEach((f) => {
      next[f.key] = ap[f.key] ?? '';
    });
    next.professional_bio = ap.professional_bio ?? '';
    next.previous_firms = ap.previous_firms ?? '';
    next.notable_experience = ap.notable_experience ?? '';
    setAdv(next);
  }

  function flash(msg) {
    setNotice(msg);
    setTimeout(() => setNotice(''), 2600);
  }

  async function saveBasic() {
    setSavingBasic(true);
    setError('');
    try {
      apply(await updateMyProfile(token, basic));
      flash('Profile saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingBasic(false);
    }
  }

  async function saveAdvocate() {
    setSavingAdv(true);
    setError('');
    try {
      // Empty strings become null, and the numeric fields are sent as
      // numbers - the API rejects "" for an integer field, which is what an
      // untouched number input gives you.
      const payload = {};
      Object.entries(adv).forEach(([k, v]) => {
        if (k === 'is_listed') {
          payload[k] = !!v;
        } else if (v === '' || v === null) {
          payload[k] = null;
        } else if (k === 'years_experience' || k === 'llb_year') {
          const n = Number(v);
          payload[k] = Number.isFinite(n) ? n : null;
        } else {
          payload[k] = String(v).trim() || null;
        }
      });
      await saveAdvocateProfile(token, payload);
      apply(await fetchMyProfile(token));
      flash('Professional profile saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingAdv(false);
    }
  }

  async function onPickFile(e) {
    const file = e.target.files?.[0];
    // Reset immediately so picking the same file twice still fires onChange.
    e.target.value = '';
    if (!file) return;

    setAvatarError('');
    // Checked here as well as on the server: a 2 MB limit the user hits
    // after a slow upload is a worse experience than one caught instantly.
    if (!IMAGE_TYPES.includes(file.type)) {
      setAvatarError('Choose a JPEG, PNG or WebP image.');
      return;
    }
    if (file.size > MAX_AVATAR_MB * 1024 * 1024) {
      setAvatarError(`That image is too large. Keep it under ${MAX_AVATAR_MB} MB.`);
      return;
    }

    setAvatarBusy(true);
    try {
      apply(await uploadAvatar(token, file));
      flash('Picture updated.');
    } catch (err) {
      setAvatarError(err.message);
    } finally {
      setAvatarBusy(false);
    }
  }

  async function removeAvatar() {
    setAvatarBusy(true);
    setAvatarError('');
    try {
      apply(await deleteAvatar(token));
      flash('Picture removed.');
    } catch (err) {
      setAvatarError(err.message);
    } finally {
      setAvatarBusy(false);
    }
  }

  return (
    <NetworkPage active="profile">
      <div className="page-scroll">
        <div className="page-wrap nx-narrow">
          <div className="page-head">
            <div>
              <h1>My Profile</h1>
              <p className="page-sub">
                {isAdvocate
                  ? 'Your professional profile is what clients see in Find an Advocate.'
                  : 'Advocates see your name, city and picture when you send a request.'}
              </p>
            </div>
          </div>

          {loading ? (
            <Skeleton rows={2} />
          ) : (
            <>
              {notice && <div className="nx-notice">{notice}</div>}
              {error && <div className="form-error">{error}</div>}

              <section className="nx-section">
                <h2>Picture</h2>
                <div className="nx-avatar-editor">
                  <Avatar user={profile} size={88} />
                  <div className="nx-avatar-controls">
                    <div className="nx-avatar-btns">
                      <button
                        type="button"
                        className="btn btn-ghost sm"
                        disabled={avatarBusy}
                        onClick={() => fileRef.current?.click()}
                      >
                        {avatarBusy
                          ? 'Uploading\u2026'
                          : profile?.avatar_url
                          ? 'Replace'
                          : 'Upload'}
                      </button>
                      {profile?.avatar_url && (
                        <button
                          type="button"
                          className="btn btn-ghost sm danger"
                          disabled={avatarBusy}
                          onClick={removeAvatar}
                        >
                          Remove
                        </button>
                      )}
                    </div>
                    <p className="nx-fine">
                      JPEG, PNG or WebP, under {MAX_AVATAR_MB} MB. Shown in
                      search results, requests and chat.
                    </p>
                    {avatarError && <div className="form-error">{avatarError}</div>}
                  </div>
                  <input
                    ref={fileRef}
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    hidden
                    onChange={onPickFile}
                  />
                </div>
              </section>

              <section className="nx-section">
                <h2>
                  Account
                  <DemoBadge user={profile} />
                </h2>
                <div className="nx-form-grid">
                  <Text
                    label="Full name"
                    value={basic.name}
                    onChange={(v) => setBasic((b) => ({ ...b, name: v }))}
                    maxLength={120}
                  />
                  <Text label="Email" value={profile?.email || ''} disabled />
                  <Text
                    label="City"
                    value={basic.city}
                    onChange={(v) => setBasic((b) => ({ ...b, city: v }))}
                    placeholder="Indore"
                    maxLength={120}
                  />
                  <Text
                    label="State"
                    value={basic.state}
                    onChange={(v) => setBasic((b) => ({ ...b, state: v }))}
                    placeholder="Madhya Pradesh"
                    maxLength={120}
                  />
                </div>
                <div className="field">
                  <label htmlFor="nx-bio">About</label>
                  <textarea
                    id="nx-bio"
                    className="nx-textarea"
                    rows={3}
                    maxLength={2000}
                    value={basic.bio}
                    onChange={(e) => setBasic((b) => ({ ...b, bio: e.target.value }))}
                    placeholder={
                      isAdvocate
                        ? 'A line about your practice.'
                        : 'Optional. Nothing about your case \u2014 that stays in your conversations.'
                    }
                  />
                </div>
                <p className="nx-fine">
                  Your conversations and case details are never shown on your
                  profile.
                </p>
                <div className="nx-dialog-actions left">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={savingBasic}
                    onClick={saveBasic}
                  >
                    {savingBasic ? 'Saving\u2026' : 'Save changes'}
                  </button>
                </div>
              </section>

              {isAdvocate && (
                <section className="nx-section">
                  <h2>Professional profile</h2>
                  <p className="nx-fine">
                    You appear in Find an Advocate only once this is filled in.
                  </p>

                  <label className="nx-switch">
                    <input
                      type="checkbox"
                      checked={!!adv.is_listed}
                      onChange={(e) =>
                        setAdv((a) => ({ ...a, is_listed: e.target.checked }))
                      }
                    />
                    <span>
                      List me in the advocate directory
                      <span className="nx-fine">
                        Turn this off to stop appearing in search. Existing
                        clients keep their conversations.
                      </span>
                    </span>
                  </label>

                  <div className="nx-form-grid">
                    {ADVOCATE_FIELDS.map((f) => (
                      <Text
                        key={f.key}
                        label={f.label}
                        hint={f.hint}
                        type={f.type}
                        min={f.min}
                        max={f.max}
                        placeholder={f.placeholder}
                        value={adv[f.key] ?? ''}
                        onChange={(v) => setAdv((a) => ({ ...a, [f.key]: v }))}
                      />
                    ))}
                  </div>

                  <div className="field">
                    <label htmlFor="nx-prof-bio">Professional bio</label>
                    <textarea
                      id="nx-prof-bio"
                      className="nx-textarea"
                      rows={4}
                      maxLength={3000}
                      value={adv.professional_bio ?? ''}
                      onChange={(e) =>
                        setAdv((a) => ({ ...a, professional_bio: e.target.value }))
                      }
                      placeholder="What you practise, the kind of matters you take, and where you appear."
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="nx-prev">Previous firms or chambers</label>
                    <textarea
                      id="nx-prev"
                      className="nx-textarea"
                      rows={2}
                      maxLength={2000}
                      value={adv.previous_firms ?? ''}
                      onChange={(e) =>
                        setAdv((a) => ({ ...a, previous_firms: e.target.value }))
                      }
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="nx-notable">Notable experience</label>
                    <textarea
                      id="nx-notable"
                      className="nx-textarea"
                      rows={2}
                      maxLength={2000}
                      value={adv.notable_experience ?? ''}
                      onChange={(e) =>
                        setAdv((a) => ({ ...a, notable_experience: e.target.value }))
                      }
                    />
                  </div>

                  <div className="nx-dialog-actions left">
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={savingAdv}
                      onClick={saveAdvocate}
                    >
                      {savingAdv ? 'Saving\u2026' : 'Save professional profile'}
                    </button>
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </NetworkPage>
  );
}

function Text({
  label, hint, value, onChange, placeholder, disabled, type = 'text',
  min, max, maxLength,
}) {
  return (
    <div className="field nx-field">
      <div className="field-label-row">
        <label>{label}</label>
        {hint && <span className="nx-hint">{hint}</span>}
      </div>
      <input
        type={type}
        value={value}
        min={min}
        max={max}
        maxLength={maxLength}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => onChange && onChange(e.target.value)}
      />
    </div>
  );
}