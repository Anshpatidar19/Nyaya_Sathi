import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

/**
 * React Router's client-side navigation does not repeat the browser's native
 * "scroll to #hash" behaviour, because the target section isn't in the DOM
 * yet at the moment navigation happens (or the app was already sitting on a
 * different page). This component watches the URL and, whenever there's a
 * hash, scrolls the matching element into view — retrying briefly in case
 * the target page is still mounting.
 */
export default function ScrollToHash() {
  const { pathname, hash } = useLocation();

  useEffect(() => {
    if (!hash) {
      window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
      return undefined;
    }

    const id = hash.replace('#', '');
    let attempts = 0;
    let timer;

    const tryScroll = () => {
      const el = document.getElementById(id);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
      attempts += 1;
      if (attempts < 20) {
        timer = setTimeout(tryScroll, 50);
      }
    };

    tryScroll();
    return () => clearTimeout(timer);
  }, [pathname, hash]);

  return null;
}