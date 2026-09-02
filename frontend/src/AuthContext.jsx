import { createContext, useContext, useEffect, useState } from 'react';
import { fetchMe, loginUser, registerUser } from './api';

const AuthContext = createContext(null);

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
    localStorage.setItem('ns_token', data.access_token);
    setToken(data.access_token);
    setUser(data.user);
    return data;
  }

  async function register(payload) {
    const data = await registerUser(payload);
    localStorage.setItem('ns_token', data.access_token);
    setToken(data.access_token);
    setUser(data.user);
    return data;
  }

  function logout() {
    localStorage.removeItem('ns_token');
    setToken(null);
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ token, user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
