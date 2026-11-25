import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { KeyRound, ShieldAlert, ShieldCheck, Smartphone } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { Link } from 'react-router-dom';
import { paths, type LoginLocationState } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem, useShake } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CountdownRing } from '@/components/ui/CountdownRing';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { OtpInput, type OtpInputHandle } from '@/components/ui/OtpInput';
import { messages } from '@/i18n';
import { classifyMfaError, mfaVerifyRequest } from '../api/login';
import { LockoutAlert } from '../components/LockoutAlert';
import { mfaChallengeStore } from '../mfa/challengeStore';
import { backupCodeSchema, totpCodeSchema, type BackupCodeValues } from '../schemas';
import { useSession } from '../session/SessionProvider';

const t = messages.mfa;

type Status = 'active' | 'expired' | 'exhausted';
type Mode = 'totp' | 'backup';

interface Throttle {
  until: number | null;
  totalSeconds: number;
}

function IconBadge({ tone, children }: { tone: 'primary' | 'warning'; children: ReactNode }) {
  return (
    <span
      className={
        tone === 'primary'
          ? 'flex size-12 items-center justify-center rounded-xl bg-accent text-accent-foreground'
          : 'flex size-12 items-center justify-center rounded-xl bg-warning/15 text-warning'
      }
    >
      {children}
    </span>
  );
}

/** Restart-required screen for expired or exhausted challenges. */
function ChallengeEnded({ status }: { status: Exclude<Status, 'active'> }) {
  const state: LoginLocationState = { notice: 'mfa_expired' };
  return (
    <AuthLayout
      icon={
        <IconBadge tone="warning">
          <ShieldAlert className="size-6" aria-hidden />
        </IconBadge>
      }
      title={status === 'expired' ? t.expiredTitle : t.exhaustedTitle}
      description={status === 'expired' ? t.expiredBody : t.exhaustedBody}
    >
      <StaggerItem>
        <Button asChild block size="lg">
          <Link to={paths.login} replace state={state}>
            {t.backToLogin}
          </Link>
        </Button>
      </StaggerItem>
    </AuthLayout>
  );
}

export function MfaChallengePage() {
  const [challenge] = useState(() => mfaChallengeStore.get());
  const [status, setStatus] = useState<Status>(() =>
    challenge && challenge.expiresAt > Date.now() ? 'active' : 'expired',
  );
  const [mode, setMode] = useState<Mode>('totp');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [attemptsRemaining, setAttemptsRemaining] = useState(challenge?.attemptsRemaining ?? 0);
  const [throttle, setThrottle] = useState<Throttle | null>(null);
  const otpRef = useRef<OtpInputHandle>(null);
  const { establishSession } = useSession();
  const { controls, shake } = useShake();

  const backupForm = useForm<BackupCodeValues>({
    resolver: zodResolver(backupCodeSchema),
    defaultValues: { code: '' },
  });

  const verify = useMutation({
    mutationFn: (value: string) => {
      if (!challenge) throw new Error('No MFA challenge');
      return mfaVerifyRequest(challenge.mfaToken, value);
    },
  });

  // Leaving the page (back button, success) drops the challenge from memory.
  useEffect(() => () => mfaChallengeStore.clear(), []);

  const end = (next: Exclude<Status, 'active'>) => {
    mfaChallengeStore.clear();
    setStatus(next);
  };

  const submit = async (value: string) => {
    if (verify.isPending || status !== 'active') return;
    setError(null);
    try {
      const accessToken = await verify.mutateAsync(value);
      mfaChallengeStore.clear();
      // <PublicOnly> redirects to the saved destination once the session exists.
      establishSession(accessToken);
    } catch (failure) {
      const classified = classifyMfaError(failure);
      switch (classified.kind) {
        case 'expired':
          end('expired');
          return;
        case 'exhausted':
          end('exhausted');
          return;
        case 'throttled': {
          const seconds = classified.retryAfterSeconds;
          setThrottle({ until: seconds ? Date.now() + seconds * 1000 : null, totalSeconds: seconds ?? 0 });
          break;
        }
        case 'replay':
          setError(t.replay);
          break;
        case 'invalid': {
          const left = classified.attemptsRemaining ?? attemptsRemaining - 1;
          if (left <= 0) {
            end('exhausted');
            return;
          }
          setAttemptsRemaining(left);
          mfaChallengeStore.setAttemptsRemaining(left);
          setError(`${t.invalidCode} ${t.attemptsLeft(left)}`);
          break;
        }
        case 'other':
          setError(classified.message);
          break;
      }
      shake();
      setCode('');
      backupForm.reset({ code: '' });
      window.setTimeout(() => {
        if (mode === 'totp') otpRef.current?.focus();
        else backupForm.setFocus('code');
      }, 50);
    }
  };

  if (status !== 'active' || !challenge) return <ChallengeEnded status={status === 'active' ? 'expired' : status} />;

  const throttled = throttle !== null && throttle.until !== null;
  const disabled = verify.isPending || throttled;

  const switchMode = () => {
    setError(null);
    setCode('');
    backupForm.reset({ code: '' });
    setMode((current) => (current === 'totp' ? 'backup' : 'totp'));
  };

  return (
    <AuthLayout
      icon={
        <div className="flex items-center justify-between">
          <IconBadge tone="primary">
            <ShieldCheck className="size-6" aria-hidden />
          </IconBadge>
          <div className="flex items-center gap-3 text-right">
            <span className="text-xs font-medium text-muted-foreground">{t.expiresIn}</span>
            <CountdownRing
              endsAt={challenge.expiresAt}
              totalSeconds={challenge.totalSeconds}
              size={52}
              onComplete={() => end('expired')}
            />
          </div>
        </div>
      }
      title={t.title}
      description={mode === 'totp' ? t.description : t.backupDescription}
    >
      <motion.div animate={controls} className="space-y-5">
        <div className="space-y-3 empty:hidden">
          <AnimatePresence mode="popLayout">
            {throttle && (
              <LockoutAlert
                key="throttle"
                until={throttle.until}
                totalSeconds={throttle.totalSeconds}
                onExpire={() => setThrottle(null)}
              />
            )}
            {error && !throttle && (
              <Alert key="error" variant="destructive" title={error} />
            )}
          </AnimatePresence>
        </div>

        <AnimatePresence mode="wait" initial={false}>
          {mode === 'totp' ? (
            <motion.div
              key="totp"
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 12 }}
              transition={{ duration: 0.2 }}
              className="space-y-5"
            >
              <p id="mfa-code-label" className="text-sm font-medium">
                {t.codeLabel}
              </p>
              <OtpInput
                ref={otpRef}
                value={code}
                onChange={(next) => {
                  setCode(next);
                  if (error) setError(null);
                }}
                onComplete={(value) => {
                  if (totpCodeSchema.safeParse(value).success) void submit(value);
                }}
                disabled={disabled}
                invalid={Boolean(error)}
                autoFocus
                labelledBy="mfa-code-label"
              />
              <Button
                type="button"
                block
                size="lg"
                loading={verify.isPending}
                loadingText={t.verifying}
                disabled={code.length !== 6 || throttled}
                onClick={() => void submit(code)}
              >
                {t.submit}
              </Button>
            </motion.div>
          ) : (
            <motion.form
              key="backup"
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -12 }}
              transition={{ duration: 0.2 }}
              noValidate
              className="space-y-5"
              onSubmit={backupForm.handleSubmit(
                (values) => void submit(backupCodeSchema.parse(values).code),
                () => shake(),
              )}
            >
              <Field
                id="mfa-backup"
                label={t.backupLabel}
                error={backupForm.formState.errors.code?.message}
              >
                {(control) => (
                  <Input
                    {...control}
                    {...backupForm.register('code')}
                    autoComplete="one-time-code"
                    autoCapitalize="characters"
                    spellCheck={false}
                    placeholder={t.backupPlaceholder}
                    className="font-mono uppercase tracking-[0.2em]"
                    disabled={disabled}
                    autoFocus
                  />
                )}
              </Field>
              <Button
                type="submit"
                block
                size="lg"
                loading={verify.isPending}
                loadingText={t.verifying}
                disabled={throttled}
              >
                {t.submit}
              </Button>
            </motion.form>
          )}
        </AnimatePresence>

        <div className="flex flex-col items-center gap-3 pt-1 text-sm">
          <button
            type="button"
            onClick={switchMode}
            className="inline-flex items-center gap-2 font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 rounded"
          >
            {mode === 'totp' ? (
              <KeyRound className="size-4" aria-hidden />
            ) : (
              <Smartphone className="size-4" aria-hidden />
            )}
            {mode === 'totp' ? t.useBackup : t.useApp}
          </button>
          <Link to={paths.login} replace className="text-muted-foreground hover:text-foreground">
            {t.backToLogin}
          </Link>
        </div>
      </motion.div>
    </AuthLayout>
  );
}
