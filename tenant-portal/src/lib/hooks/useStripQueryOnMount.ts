import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

/**
 * Removes the query string (single-use tokens, allauth params) from the
 * address bar once, when the page mounts. Only once: while a page plays its
 * exit animation it re-renders with the *next* location, and stripping then
 * would wipe the next page's query (e.g. a `?tab=` return target).
 */
export function useStripQueryOnMount(): void {
  const location = useLocation();
  const navigate = useNavigate();
  const initial = useRef({ pathname: location.pathname, search: location.search });

  useEffect(() => {
    const { pathname, search } = initial.current;
    if (search && window.location.pathname === pathname && window.location.search === search) {
      navigate(pathname, { replace: true });
    }
  }, [navigate]);
}
