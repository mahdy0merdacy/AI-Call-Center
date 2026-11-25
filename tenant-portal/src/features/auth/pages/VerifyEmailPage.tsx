import { motion } from 'framer-motion';
import { Link2Off, MailCheck, RefreshCw, TimerOff } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { paths, type LoginLocationState } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem } from '@/components/motion';
import { AnimatedCheck } from '@/components/ui/AnimatedCheck';
import { Button } from '@/components/ui/button';
import { WaveformVisual } from '@/components/ui/WaveformVisual';
import { messages } from '@/i18n';
import { useCountdown } from '@/lib/hooks/useCountdown';
import { useStripQueryOnMount } from '@/lib/hooks/useStripQueryOnMount';
import { classifyVerifyError, forgetVerification, verifyEmailOnce } from '../api/registration';
import { VerifyEmailModal } from '../components/VerifyEmailModal';
import { useSession } from '../session/SessionProvider';

const t = messages.verifyEmail;
const REDIRECT_SECONDS = 5;

type State = 'loading' | 'success' | 'expired' | 'used' | 'invalid' | 'transient';

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

export function VerifyEmailPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { status } = useSession();

  // Read the link once, then strip uid/token from the address bar so the
  // single-use token doesn't linger in history or leak via Referer.
  const [link] = useState(() => {
    const params = new URLSearchParams(location.search);
    return { uid: params.get('uid'), token: params.get('token') };
  });
  const [state, setState] = useState<State>(link.uid && link.token ? 'loading' : 'invalid');
  const [transientMessage, setTransientMessage] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [redirectAt, setRedirectAt] = useState<number | null>(null);

  useStripQueryOnMount();

  useEffect(() => {
    const { uid, token } = link;
    if (!uid || !token) return;
    let active = true;
    setState('loading');
    verifyEmailOnce(uid, token)
      .then(() => {
        if (!active) return;
        setState('success');
        setRedirectAt(Date.now() + REDIRECT_SECONDS * 1000);
      })
      .catch((error: unknown) => {
        const failure = classifyVerifyError(error);
        if (failure.kind === 'transient') {
          forgetVerification(uid, token); // allow "Try again"
          setTransientMessage(failure.message);
        }
        if (active) setState(failure.kind);
      });
    return () => {
      active = false;
    };
  }, [link, attempt]);

  const goOn = () => {
    if (status === 'authenticated') {
      navigate(paths.dashboard, { replace: true });
      return;
    }
    const state: LoginLocationState = { notice: 'email_verified' };
    navigate(paths.login, { replace: true, state });
  };

  const secondsLeft = useCountdown(redirectAt, goOn);

  if (state === 'loading') {
    return (
      <AuthLayout title={t.loadingTitle} description={t.loadingBody}>
        <StaggerItem>
          <div role="status" aria-label={t.loadingTitle} className="h-16">
            <WaveformVisual bars={40} />
          </div>
        </StaggerItem>
      </AuthLayout>
    );
  }

  if (state === 'success') {
    return (
      <AuthLayout icon={<AnimatedCheck size={64} />} title={t.successTitle} description={t.successBody}>
        <StaggerItem className="space-y-3">
          <Button block size="lg" onClick={goOn}>
            {status === 'authenticated' ? t.goToDashboard : t.continue}
          </Button>
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.8 }}
            className="text-center text-sm text-muted-foreground"
            aria-live="polite"
          >
            {t.redirecting(secondsLeft)}
          </motion.p>
        </StaggerItem>
      </AuthLayout>
    );
  }

  if (state === 'transient') {
    return (
      <AuthLayout
        icon={
          <Badge tone="destructive">
            <RefreshCw className="size-6" aria-hidden />
          </Badge>
        }
        title={t.loadingTitle}
        description={transientMessage}
      >
        <StaggerItem>
          <Button block size="lg" onClick={() => setAttempt((value) => value + 1)}>
            {messages.common.retry}
          </Button>
        </StaggerItem>
      </AuthLayout>
    );
  }

  const copy = {
    expired: { title: t.expiredTitle, body: t.expiredBody, icon: <TimerOff className="size-6" aria-hidden /> },
    used: { title: t.usedTitle, body: t.usedBody, icon: <MailCheck className="size-6" aria-hidden /> },
    invalid: { title: t.invalidTitle, body: t.invalidBody, icon: <Link2Off className="size-6" aria-hidden /> },
  }[state];

  return (
    <AuthLayout
      icon={<Badge tone={state === 'invalid' ? 'destructive' : 'warning'}>{copy.icon}</Badge>}
      title={copy.title}
      description={copy.body}
    >
      <StaggerItem className="space-y-3">
        {state === 'used' && (
          <Button asChild block size="lg">
            <Link to={paths.login}>{messages.register.signIn}</Link>
          </Button>
        )}
        <Button block size="lg" variant={state === 'used' ? 'outline' : 'default'} onClick={() => setModalOpen(true)}>
          {t.requestNew}
        </Button>
        {state !== 'used' && (
          <Button asChild block size="lg" variant="ghost">
            <Link to={paths.login}>{messages.register.backToLogin}</Link>
          </Button>
        )}
      </StaggerItem>
      <VerifyEmailModal open={modalOpen} onOpenChange={setModalOpen} />
    </AuthLayout>
  );
}
