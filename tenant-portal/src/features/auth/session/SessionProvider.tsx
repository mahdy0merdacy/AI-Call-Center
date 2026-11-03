import { useQueryClient } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { handleRefreshFailure, isSuspendedError, refreshAccessToken } from '@/lib/apiClient';
import { authEvents, broadcastSignOut, onRemoteSignOut } from '@/lib/auth/authEvents';
import { readTokenExpiry } from '@/lib/auth/jwt';
import { tokenStore } from '@/lib/auth/tokenStore';
import { logoutRequest } from '../api/session';

export type SessionStatus = 'booting' | 'authenticated' | 'anonymous';

interface SessionContextValue {
  status: SessionStatus;
  /** Store a freshly issued access token (login, MFA, Google exchange, onboarding). */
  establishSession: (accessToken: string) => void;
  /** Server logout (best effort) + local cleanup. */
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/** Refresh this long before the access token expires. */
const REFRESH_LEEWAY_MS = 60_000;
const MIN_REFRESH_DELAY_MS = 5_000;

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const token = useSyncExternalStore(tokenStore.subscribe, tokenStore.get, tokenStore.get);
  const [booting, setBooting] = useState(true);

  // Boot: restore the session from the HttpOnly refresh cookie. StrictMode runs
  // this twice in dev; refreshAccessToken() dedupes into a single request.
  useEffect(() => {
    let active = true;
    refreshAccessToken()
      .catch((error: unknown) => {
        // No cookie / expired cookie is the normal anonymous case.
        if (isSuspendedError(error)) authEvents.emit('tenant-suspended');
      })
      .finally(() => {
        if (active) setBooting(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // Proactive background refresh shortly before expiry so requests rarely hit a 401.
  useEffect(() => {
    if (!token) return;
    const exp = readTokenExpiry(token);
    if (exp === null) return;
    const delay = Math.max(MIN_REFRESH_DELAY_MS, exp * 1000 - Date.now() - REFRESH_LEEWAY_MS);
    const id = window.setTimeout(() => {
      refreshAccessToken().catch(handleRefreshFailure);
    }, delay);
    return () => window.clearTimeout(id);
  }, [token]);

  // Whenever the token disappears (logout, expiry, suspension) drop cached server state.
  const previousToken = useRef(token);
  useEffect(() => {
    if (previousToken.current && !token) queryClient.clear();
    previousToken.current = token;
  }, [token, queryClient]);

  // Another tab signed out: mirror it here.
  useEffect(
    () =>
      onRemoteSignOut(() => {
        tokenStore.clear();
        authEvents.emit('signed-out');
      }),
    [],
  );

  const establishSession = useCallback(
    (accessToken: string) => {
      // New identity: never show the previous user's cached data.
      queryClient.clear();
      tokenStore.set(accessToken);
    },
    [queryClient],
  );

  const signOut = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      // Even if the server call fails we still clear local state; the
      // refresh cookie expires on its own and is single-use anyway.
    }
    tokenStore.clear();
    queryClient.clear();
    broadcastSignOut();
    authEvents.emit('signed-out');
  }, [queryClient]);

  const status: SessionStatus = booting ? 'booting' : token ? 'authenticated' : 'anonymous';

  const value = useMemo(
    () => ({ status, establishSession, signOut }),
    [status, establishSession, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error('useSession must be used inside <SessionProvider>');
  return context;
}
