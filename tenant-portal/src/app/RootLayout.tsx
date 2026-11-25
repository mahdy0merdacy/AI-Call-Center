import { AnimatePresence } from 'framer-motion';
import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useOutlet } from 'react-router-dom';
import { NetworkBanner } from '@/components/feedback/NetworkBanner';
import { SplashScreen } from '@/components/feedback/SplashScreen';
import { PageTransition } from '@/components/motion';
import { useSession } from '@/features/auth/session/SessionProvider';
import { messages } from '@/i18n';
import { authEvents } from '@/lib/auth/authEvents';
import { env } from '@/lib/env';
import { paths, type LoginLocationState } from './paths';

// Mock mode only: scenario triggers + the inbox of emails the mock backend "sent".
const MockDevtools = env.enableMocks ? lazy(() => import('@/mocks/MockDevtools')) : null;

/** Keeps the outgoing route rendered while its exit animation plays. */
function FrozenOutlet() {
  const outlet = useOutlet();
  const [frozen] = useState(outlet);
  return frozen;
}

/** Bridges auth events raised outside React (interceptors) to navigation. */
function AuthEventRouter() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const from = `${location.pathname}${location.search}`;
    const offExpired = authEvents.on('session-expired', () => {
      const state: LoginLocationState = { notice: 'session_expired', from };
      navigate(paths.login, { replace: true, state });
    });
    const offSuspended = authEvents.on('tenant-suspended', () => {
      navigate(paths.suspended, { replace: true });
    });
    const offSignedOut = authEvents.on('signed-out', () => {
      const state: LoginLocationState = { notice: 'signed_out' };
      navigate(paths.login, { replace: true, state });
    });
    return () => {
      offExpired();
      offSuspended();
      offSignedOut();
    };
  }, [navigate, location.pathname, location.search]);

  return null;
}

/** Moves focus to the new page's main region after navigation (screen readers, keyboard). */
function RouteFocus() {
  const { pathname } = useLocation();
  const previous = useRef(pathname);

  useEffect(() => {
    if (previous.current === pathname) return;
    previous.current = pathname;
    // Wait for the page transition to mount the new <main>.
    const id = window.setTimeout(() => {
      const target = document.getElementById('main') ?? document.querySelector('h1');
      if (target instanceof HTMLElement) target.focus({ preventScroll: true });
      window.scrollTo({ top: 0 });
    }, 320);
    return () => window.clearTimeout(id);
  }, [pathname]);

  return null;
}

export function RootLayout() {
  const location = useLocation();
  const { status } = useSession();

  return (
    <>
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        {messages.common.skipToContent}
      </a>
      <AuthEventRouter />
      <RouteFocus />
      <NetworkBanner />
      {status === 'booting' ? (
        <SplashScreen />
      ) : (
        <AnimatePresence mode="wait" initial={false}>
          <PageTransition key={location.pathname}>
            <FrozenOutlet />
          </PageTransition>
        </AnimatePresence>
      )}
      {MockDevtools && (
        <Suspense fallback={null}>
          <MockDevtools />
        </Suspense>
      )}
    </>
  );
}
