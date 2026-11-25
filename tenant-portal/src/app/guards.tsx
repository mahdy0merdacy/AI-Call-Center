/**
 * Route guards, used as layout routes (`<Route element={<RequireAuth />}>`).
 * Order for authenticated pages: RequireAuth → RequireCompleteAccount → RequireRole/RequireTenant.
 */
import { useIsPresent } from 'framer-motion';
import { createContext, useContext, useEffect, useRef } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { FullPageError } from '@/components/feedback/FullPageError';
import { SplashScreen } from '@/components/feedback/SplashScreen';
import { useCurrentUser } from '@/features/auth/hooks/useCurrentUser';
import { useSession } from '@/features/auth/session/SessionProvider';
import type { CurrentUser, Role } from '@/features/auth/types';
import { messages } from '@/i18n';
import { isSuspendedError } from '@/lib/apiClient';
import { paths, type LoginLocationState } from './paths';
import { safeReturnTo } from './redirects';

const AuthedUserContext = createContext<CurrentUser | null>(null);

/** The signed-in user. Only valid below <RequireAuth>. */
export function useAuthedUser(): CurrentUser {
  const user = useContext(AuthedUserContext);
  if (!user) throw new Error('useAuthedUser must be used below <RequireAuth>');
  return user;
}

/**
 * Pages stay mounted while their exit animation plays, and keep re-rendering
 * with the *new* location. A guard in a leaving page must never redirect, or
 * it overrides the navigation that is already happening (e.g. losing the
 * "return to" target). It just keeps showing its content until unmounted.
 */
function useLeaving(): boolean {
  return !useIsPresent();
}

/** Sign-in pages: a signed-in user goes to where they were heading, or the dashboard. */
export function PublicOnly() {
  const { status } = useSession();
  const location = useLocation();
  const leaving = useLeaving();
  if (status === 'authenticated' && !leaving) {
    const from = (location.state as LoginLocationState | null)?.from;
    return <Navigate to={safeReturnTo(from)} replace />;
  }
  return <Outlet />;
}

/**
 * Requires a session (restored on boot by SessionProvider) and a loaded
 * /auth/me/. Anonymous visitors go to /login and come back afterwards.
 */
export function RequireAuth() {
  const { status } = useSession();
  const location = useLocation();
  const me = useCurrentUser();
  const isPresent = !useLeaving();
  const navigate = useNavigate();
  const hadSession = useRef(status === 'authenticated');
  if (status === 'authenticated') hadSession.current = true;
  const sessionEnded = status !== 'authenticated' && hadSession.current;

  // A session that ends here (sign-out, expiry, suspension) always comes with
  // an auth event that navigates with the right notice. Redirecting here too
  // would overwrite that notice, so only do it if no event navigated us away.
  useEffect(() => {
    if (!sessionEnded || !isPresent) return;
    const id = window.setTimeout(() => navigate(paths.login, { replace: true }), 1000);
    return () => window.clearTimeout(id);
  }, [sessionEnded, isPresent, navigate]);

  if (status !== 'authenticated') {
    if (sessionEnded || !isPresent) return null;
    const state: LoginLocationState = { from: `${location.pathname}${location.search}` };
    return <Navigate to={paths.login} replace state={state} />;
  }
  // A suspension is routed globally by the API client; keep the splash meanwhile.
  if (me.isPending || (me.isError && isSuspendedError(me.error))) {
    return <SplashScreen label={messages.guards.loadingAccount} />;
  }
  // A failed *refetch* keeps the cached user: the page stays usable and the
  // network banner explains the problem. Only a first load with no data blocks.
  if (me.isError && !me.data) {
    return (
      <FullPageError
        title={messages.guards.accountErrorTitle}
        error={me.error}
        onRetry={() => void me.refetch()}
        retrying={me.isFetching}
      />
    );
  }
  return (
    <AuthedUserContext.Provider value={me.data}>
      <Outlet />
    </AuthedUserContext.Provider>
  );
}

/**
 * Mirrors the backend's AccountSetupComplete permission: a Google user
 * without a company must onboard, a platform admin must enrol MFA first.
 */
export function RequireCompleteAccount() {
  const user = useAuthedUser();
  if (useLeaving()) return <Outlet />;
  if (user.role === 'platform_admin' && !user.mfa_enabled) {
    return <Navigate to={paths.mfaSetup} replace />;
  }
  if (user.role === 'tenant_admin' && user.tenant_id === null) {
    return <Navigate to={paths.onboarding} replace />;
  }
  return <Outlet />;
}

/** /onboarding only makes sense for a tenant admin without a company (Google sign-up). */
export function RequireOnboardingPending() {
  const user = useAuthedUser();
  if (useLeaving()) return <Outlet />;
  if (user.role !== 'tenant_admin' || user.tenant_id !== null) {
    return <Navigate to={paths.dashboard} replace />;
  }
  return <Outlet />;
}

/** /mfa/setup only for a platform admin who hasn't enrolled MFA yet. */
export function RequireMfaSetupPending() {
  const user = useAuthedUser();
  if (useLeaving()) return <Outlet />;
  if (user.role !== 'platform_admin' || user.mfa_enabled) {
    return <Navigate to={paths.dashboard} replace />;
  }
  return <Outlet />;
}

export function RequireRole({ role }: { role: Role }) {
  const user = useAuthedUser();
  const leaving = useLeaving();
  return user.role === role || leaving ? <Outlet /> : <Navigate to={paths.forbidden} replace />;
}

export function RequireTenant() {
  const user = useAuthedUser();
  const leaving = useLeaving();
  return user.tenant_id !== null || leaving ? <Outlet /> : <Navigate to={paths.forbidden} replace />;
}
