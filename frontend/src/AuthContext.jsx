import { createContext, useContext, useEffect, useState } from 'react';
import { fetchMe, loginUser, registerUser } from './api';

const AuthContext = createContext(null);

// Which thread the Ask page reopens on a refresh. It lives here rather than
// in the page because it is session-scoped: it has to be cleared whenever the
// session changes hands, and only this file knows when that happens.
export const ACTIVE_CONVERSATION_KEY = 'ns_active_conversation';

// Signing in or out starts a clean slate. Without this, logging in reopened
// whatever thread was last read on this browser - including one belonging to
// the previous account - so the first question of the session landed inside
// someone else's conversation instead of a new one.
function clearActiveConversation() {
  localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('ns_token'));
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    fetchMe(token)
      .then(setUser)
      .catch(() => {
        setToken(null);
        localStorage.removeItem('ns_token');
      })
      .finally(() => setLoading(false));
  }, [token]);

  async function login(credentials) {
    const data = await loginUser(credentials);
    // Before the token lands: the Ask page restores off this key the moment
    // it sees a token, so clearing it afterwards would be a race.
    clearActiveConversation();
    localStorage.setItem('ns_token', data.access_token);
    setToken(data.access_token);
    setUser(data.user);
    return data;
  }

  async function register(payload) {
    const data = await registerUser(payload);
    // With email confirmation on there is no session yet - the caller shows
    // a "check your inbox" screen instead of navigating into the app.
    if (data.access_token) {
      clearActiveConversation();
      localStorage.setItem('ns_token', data.access_token);
      setToken(data.access_token);
      setUser(data.user);
    }
    return data;
  }

  function logout() {
    clearActiveConversation();
    localStorage.removeItem('ns_token');
    setToken(null);
    setUser(null);
  }

  // Everything role-dependent reads these two, so the check lives in one place.
  const role = user?.role === 'advocate' ? 'advocate' : 'user';
  const isAdvocate = role === 'advocate';

  return (
    <AuthContext.Provider
      value={{ token, user, role, isAdvocate, loading, login, register, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}