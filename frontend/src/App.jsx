import { Routes, Route, useLocation } from 'react-router-dom';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import ScrollToHash from './components/ScrollToHash';
import ProtectedRoute from './components/ProtectedRoute';
import Landing from './pages/Landing';
import Login from './pages/Login';
import Register from './pages/Register';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import Ask from './pages/Ask';
import Matters from './pages/Matters';
import MatterDetail from './pages/MatterDetail';
import Advocates from './pages/Advocates';
import AdvocateProfile from './pages/AdvocateProfile';
import Network from './pages/Network';
import Messages from './pages/Messages';
import Profile from './pages/Profile';

// Routes that render their own self-contained app shell (own top bar, own
// nav rail, no marketing nav and no footer). Kept as a list rather than a
// chain of startsWith calls, because it now has eight entries and a missing
// one shows up as a stray marketing header above the app.
const SHELL_PREFIXES = [
  '/ask',
  '/matters',
  '/advocates',
  '/network',
  '/messages',
  '/profile',
];

export default function App() {
  const { pathname } = useLocation();
  const isAppShell = SHELL_PREFIXES.some((p) => pathname.startsWith(p));

  return (
    <>
      <ScrollToHash />
      {!isAppShell && <Navbar />}
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot" element={<ForgotPassword />} />
        <Route path="/reset" element={<ResetPassword />} />
        <Route
          path="/ask"
          element={
            <ProtectedRoute>
              <Ask />
            </ProtectedRoute>
          }
        />
        <Route
          path="/matters"
          element={
            <ProtectedRoute>
              <Matters />
            </ProtectedRoute>
          }
        />
        <Route
          path="/matters/:id"
          element={
            <ProtectedRoute>
              <MatterDetail />
            </ProtectedRoute>
          }
        />

        {/* --- advocate network --- */}
        <Route
          path="/advocates"
          element={
            <ProtectedRoute>
              <Advocates />
            </ProtectedRoute>
          }
        />
        <Route
          path="/advocates/:id"
          element={
            <ProtectedRoute>
              <AdvocateProfile />
            </ProtectedRoute>
          }
        />
        <Route
          path="/network"
          element={
            <ProtectedRoute>
              <Network />
            </ProtectedRoute>
          }
        />
        <Route
          path="/messages"
          element={
            <ProtectedRoute>
              <Messages />
            </ProtectedRoute>
          }
        />
        {/* Same page; the id just decides which conversation is open, so the
            component is not remounted when switching threads. */}
        <Route
          path="/messages/:threadId"
          element={
            <ProtectedRoute>
              <Messages />
            </ProtectedRoute>
          }
        />
        <Route
          path="/profile"
          element={
            <ProtectedRoute>
              <Profile />
            </ProtectedRoute>
          }
        />
      </Routes>
      {!isAppShell && <Footer />}
    </>
  );
}