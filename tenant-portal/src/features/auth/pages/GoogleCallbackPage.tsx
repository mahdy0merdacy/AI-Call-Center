import { CircleSlash, MailWarning, TimerOff, UserRoundX, XCircle } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { paths } from '@/app/paths';
import { safeReturnTo } from '@/app/redirects';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem } from '@/components/motion';
import { Button } from '@/components/ui/button';
import { WaveformVisual } from '@/components/ui/WaveformVisual';
import { messages } from '@/i18n';
import { useStripQueryOnMount } from '@/lib/hooks/useStripQueryOnMount';
import {
  classifyGoogleError,
  consumeGoogleReturnTo,
  exchangeGoogleSessionOnce,
  failureFromCallbackParams,
  type GoogleFailure,
} from '../api/google';
import { GoogleButton } from '../components/GoogleButton';
import { useSession } from '../session/SessionProvider';

const t = messages.google;

function Badge({ tone, children }: { tone: 'warning' | 'destructive'; children: ReactNode }) {
  return (
    <span
      className={
        tone === 'warning'
          ? 'flex size-12 items-center justify-center rounded-xl bg-warning/15 text-warning'
          : 'flex size-12 items-center justify-center rounded-xl bg-destructive/10 text-destructive'
      }
    >
      {children}
    </span>
  );
}

function UseEmailLink() {
  return (
    <Button asChild block size="lg" variant="ghost">
      <Link to={paths.login}>{t.useEmail}</Link>
    </Button>
  );
}

function FailureScreen({ failure }: { failure: GoogleFailure }) {
  switch (failure.kind) {
    case 'conflict':
      return (
        <AuthLayout
          icon={
            <Badge tone="warning">
              <UserRoundX className="size-6" aria-hidden />
            </Badge>
          }
          title={t.conflictTitle}
          description={t.conflictBody}
        >
          <StaggerItem className="space-y-3">
            <Button asChild block size="lg">
              <Link to={paths.login}>{t.conflictCta}</Link>
            </Button>
            <Button asChild block size="lg" variant="ghost">
              <Link to={paths.forgotPassword}>{t.conflictForgot}</Link>
            </Button>
          </StaggerItem>
        </AuthLayout>
      );
    case 'unverified':
      return (
        <AuthLayout
          icon={
            <Badge tone="warning">
              <MailWarning className="size-6" aria-hidden />
            </Badge>
          }
          title={t.unverifiedTitle}
          description={t.unverifiedBody}
        >
          <StaggerItem className="space-y-3">
            <Button asChild block size="lg">
              <Link to={paths.register}>{t.signUpWithEmail}</Link>
            </Button>
            <UseEmailLink />
          </StaggerItem>
        </AuthLayout>
      );
    case 'expired':
    case 'cancelled':
    case 'failed': {
      const copy = {
        expired: { title: t.expiredTitle, body: t.expiredBody, icon: <TimerOff className="size-6" aria-hidden /> },
        cancelled: { title: t.cancelledTitle, body: t.cancelledBody, icon: <CircleSlash className="size-6" aria-hidden /> },
        failed: {
          title: t.failedTitle,
          body: failure.kind === 'failed' && failure.message ? failure.message : messages.errors.unknown,
          icon: <XCircle className="size-6" aria-hidden />,
        },
      }[failure.kind];
      return (
        <AuthLayout
          icon={<Badge tone={failure.kind === 'failed' ? 'destructive' : 'warning'}>{copy.icon}</Badge>}
          title={copy.title}
          description={copy.body}
        >
          <StaggerItem className="space-y-3">
            <GoogleButton label={t.retry} />
            <UseEmailLink />
          </StaggerItem>
        </AuthLayout>
      );
    }
  }
}

export function GoogleCallbackPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { establishSession } = useSession();
  const [failure, setFailure] = useState<GoogleFailure | null>(() =>
    failureFromCallbackParams(new URLSearchParams(location.search)),
  );

  // Don't keep allauth's query parameters in the address bar.
  useStripQueryOnMount();

  useEffect(() => {
    if (failure) return;
    let active = true;
    exchangeGoogleSessionOnce()
      .then(({ accessToken, onboardingRequired }) => {
        if (!active) return;
        establishSession(accessToken);
        const returnTo = consumeGoogleReturnTo();
        // RequireCompleteAccount also sends tenant-less users to /onboarding
        // when the backend doesn't say.
        navigate(onboardingRequired ? paths.onboarding : safeReturnTo(returnTo), { replace: true });
      })
      .catch((error: unknown) => {
        if (active) setFailure(classifyGoogleError(error));
      });
    return () => {
      active = false;
    };
    // Runs once per page load (deps intentionally empty); the exchange itself is deduplicated.
  }, []);

  if (failure) return <FailureScreen failure={failure} />;

  return (
    <AuthLayout title={t.exchangingTitle} description={t.exchangingBody}>
      <StaggerItem>
        <div role="status" aria-label={t.exchangingTitle} className="h-16">
          <WaveformVisual bars={40} />
        </div>
      </StaggerItem>
    </AuthLayout>
  );
}
