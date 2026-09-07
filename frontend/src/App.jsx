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

export default function App() {
  const { pathname } = useLocation();
  // The Ask page is its own self-contained app shell (own top bar, no
  // marketing nav, no footer) rather than a marketing page, so the site
  // chrome is skipped there.
  // Matters uses the same self-contained shell as Ask.
  const isAskShell = pathname.startsWith('/ask') || pathname.startsWith('/matters');

  return (
    <>
      <ScrollToHash />
      {!isAskShell && <Navbar />}
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
      </Routes>
      {!isAskShell && <Footer />}
    </>
  );
}