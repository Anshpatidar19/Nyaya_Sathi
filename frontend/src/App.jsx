import { Routes, Route, useLocation } from 'react-router-dom';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import ScrollToHash from './components/ScrollToHash';
import ProtectedRoute from './components/ProtectedRoute';
import Landing from './pages/Landing';
import Login from './pages/Login';
import Register from './pages/Register';
import Ask from './pages/Ask';

export default function App() {
  const { pathname } = useLocation();
  // The Ask page is its own self-contained app shell (own top bar, no
  // marketing nav, no footer) rather than a marketing page, so the site
  // chrome is skipped there.
  const isAskShell = pathname.startsWith('/ask');

  return (
    <>
      <ScrollToHash />
      {!isAskShell && <Navbar />}
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route
          path="/ask"
          element={
            <ProtectedRoute>
              <Ask />
            </ProtectedRoute>
          }
        />
      </Routes>
      {!isAskShell && <Footer />}
    </>
  );
}