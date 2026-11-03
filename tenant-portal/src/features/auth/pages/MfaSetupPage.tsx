import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, ArrowLeft, Check, Copy, Download, ExternalLink, Printer, RefreshCw, ShieldCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { useAuthedUser } from '@/app/guards';
import { paths } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem, useShake } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { OtpInput, type OtpInputHandle } from '@/components/ui/OtpInput';
import { Skeleton } from '@/components/ui/skeleton';
import { messages } from '@/i18n';
import { tokenStore } from '@/lib/auth/tokenStore';
import { copyText, downloadText } from '@/lib/clipboard';
import { env } from '@/lib/env';
import { userMessage } from '@/lib/errors';
import { cn } from '@/lib/utils';
import { classifyTotpConfirmError, confirmTotpSetup, startTotpSetup } from '../api/mfaSetup';
import { LockoutAlert } from '../components/LockoutAlert';
import { authKeys } from '../queryKeys';
import { useSession } from '../session/SessionProvider';

const t = messages.mfaSetup;
type Step = 0 | 1 | 2;

function Stepper({ step }: { step: Step }) {
  return (
    <ol className="mb-7 flex items-center gap-2" aria-label={t.stepLabel(step + 1, t.steps.length, t.steps[step] ?? '')}>
      {t.steps.map((label, index) => {
        const done = index < step;
        const current = index === step;
        return (
          <li key={label} className="flex flex-1 items-center gap-2" aria-current={current ? 'step' : undefined}>
            <motion.span
              animate={{ scale: current ? 1.08 : 1 }}
              className={cn(
                'flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold transition-colors',
                done && 'bg-success text-success-foreground',
                current && 'bg-primary text-primary-foreground shadow-glow',
                !done && !current && 'bg-muted text-muted-foreground',
              )}
            >
              {done ? <Check className="size-3.5" strokeWidth={3} aria-hidden /> : index + 1}
            </motion.span>
            <span className={cn('hidden text-xs font-medium sm:inline', current ? 'text-foreground' : 'text-muted-foreground')}>
              {label}
            </span>
            {index < t.steps.length - 1 && (
              <span className="relative h-0.5 flex-1 overflow-hidden rounded-full bg-muted">
                <motion.span
                  className="absolute inset-0 origin-left bg-success"
                  initial={false}
                  animate={{ scaleX: done ? 1 : 0 }}
                  transition={{ duration: 0.4 }}
                />
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

const stepMotion = {
  initial: { opacity: 0, x: 16 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -16 },
  transition: { duration: 0.25 },
};

export function MfaSetupPage() {
  const user = useAuthedUser();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { signOut } = useSession();
  const { controls, shake } = useShake();
  const otpRef = useRef<OtpInputHandle>(null);

  const [step, setStep] = useState<Step>(0);
  const [attempt, setAttempt] = useState(0);
  const [code, setCode] = useState('');
  const [codeError, setCodeError] = useState<string | null>(null);
  const [throttle, setThrottle] = useState<{ until: number | null; totalSeconds: number } | null>(null);
  const [expired, setExpired] = useState(false);
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  const [finishing, setFinishing] = useState(false);

  // A query (not an effect) so StrictMode doesn't create two secrets; gcTime 0 = fresh key per visit.
  const setup = useQuery({
    queryKey: ['mfa', 'totp-setup', attempt],
    queryFn: startTotpSetup,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  });

  const confirm = useMutation({ mutationFn: confirmTotpSetup });

  // Backup codes are shown once: warn before closing/reloading the tab.
  useEffect(() => {
    if (step !== 2 || finishing) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = t.leaveWarning;
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [step, finishing]);

  const verify = async (value: string) => {
    if (confirm.isPending) return;
    setCodeError(null);
    try {
      const result = await confirm.mutateAsync(value);
      // Keep the cached user as is until "Continue": refetching now would let the
      // guard leave this page before the codes are saved.
      if (result.accessToken) tokenStore.set(result.accessToken);
      setBackupCodes(result.backupCodes);
      setStep(2);
    } catch (error) {
      const failure = classifyTotpConfirmError(error);
      shake();
      setCode('');
      if (failure.kind === 'expired') {
        setExpired(true);
        return;
      }
      if (failure.kind === 'throttled') {
        const seconds = failure.retryAfterSeconds;
        setThrottle({ until: seconds ? Date.now() + seconds * 1000 : null, totalSeconds: seconds ?? 0 });
        return;
      }
      setCodeError(failure.kind === 'invalid' ? t.invalidCode : failure.message);
      window.setTimeout(() => otpRef.current?.focus(), 50);
    }
  };

  const restart = () => {
    setExpired(false);
    setCodeError(null);
    setCode('');
    setStep(0);
    setAttempt((value) => value + 1);
  };

  const codesText = () =>
    [t.fileHeader(env.appName, user.email), new Date().toISOString(), '', ...backupCodes, '', t.fileNote].join('\n');

  const copy = async (text: string) => {
    if (await copyText(text)) toast.success(t.copied);
    else toast.error(t.copyFailed);
  };

  const print = () => {
    document.body.classList.add('print-backup-codes');
    const cleanup = () => document.body.classList.remove('print-backup-codes');
    window.addEventListener('afterprint', cleanup, { once: true });
    window.print();
  };

  const finish = async () => {
    setFinishing(true);
    await queryClient.refetchQueries({ queryKey: authKeys.me() });
    toast.success(t.enabledToast);
    navigate(paths.dashboard, { replace: true });
  };

  const throttled = throttle !== null && throttle.until !== null;

  return (
    <AuthLayout
      wide
      icon={
        <span className="flex size-12 items-center justify-center rounded-xl bg-accent text-accent-foreground">
          <ShieldCheck className="size-6" aria-hidden />
        </span>
      }
      title={t.title}
      description={t.description}
      footer={
        step < 2 && (
          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            {t.signOut}
          </button>
        )
      }
    >
      <StaggerItem>
        <Stepper step={step} />
      </StaggerItem>

      <motion.div animate={controls}>
        <AnimatePresence mode="wait" initial={false}>
          {step === 0 && (
            <motion.section key="scan" {...stepMotion} aria-labelledby="mfa-scan-title" className="space-y-5">
              <div>
                <h2 id="mfa-scan-title" className="font-semibold">{t.scanTitle}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{t.scanBody}</p>
              </div>

              {setup.isError ? (
                <Alert
                  variant="destructive"
                  title={t.loadFailed}
                  aside={
                    <Button size="sm" variant="outline" onClick={() => void setup.refetch()}>
                      <RefreshCw className="size-4" aria-hidden />
                      {messages.common.retry}
                    </Button>
                  }
                >
                  {userMessage(setup.error)}
                </Alert>
              ) : (
                <div className="grid items-center gap-6 sm:grid-cols-[auto_1fr]">
                  <div className="mx-auto rounded-2xl border bg-white p-3 shadow-card">
                    {setup.data ? (
                      <motion.img
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        src={setup.data.qrDataUrl}
                        alt={t.qrAlt}
                        width={176}
                        height={176}
                        className="size-44 [image-rendering:pixelated]"
                      />
                    ) : (
                      <Skeleton className="size-44 bg-slate-200" aria-label={t.loading} />
                    )}
                  </div>
                  <div className="space-y-3">
                    <p className="text-sm font-medium">{t.manualTitle}</p>
                    <p className="text-[13px] text-muted-foreground">{t.manualBody}</p>
                    {setup.data ? (
                      <div className="flex items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2">
                        <code className="min-w-0 flex-1 break-all font-mono text-sm tracking-wider">
                          {setup.data.secret.match(/.{1,4}/g)?.join(' ')}
                        </code>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="size-8 shrink-0"
                          aria-label={t.copyKey}
                          onClick={() => void copy(setup.data.secret)}
                        >
                          <Copy className="size-4" aria-hidden />
                        </Button>
                      </div>
                    ) : (
                      <Skeleton className="h-10 w-full" />
                    )}
                    {setup.data?.otpauthUri && (
                      <a
                        href={setup.data.otpauthUri}
                        className="inline-flex items-center gap-1.5 text-[13px] font-medium text-primary hover:underline"
                      >
                        <ExternalLink className="size-3.5" aria-hidden />
                        {t.openInApp}
                      </a>
                    )}
                  </div>
                </div>
              )}

              <Button block size="lg" disabled={!setup.data} onClick={() => setStep(1)}>
                {t.next}
              </Button>
            </motion.section>
          )}

          {step === 1 && (
            <motion.section key="verify" {...stepMotion} aria-labelledby="mfa-verify-title" className="space-y-5">
              <div>
                <h2 id="mfa-verify-title" className="font-semibold">{t.verifyTitle}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{t.verifyBody}</p>
              </div>

              <div className="space-y-3 empty:hidden">
                <AnimatePresence mode="popLayout">
                  {expired && (
                    <Alert
                      key="expired"
                      variant="warning"
                      title={t.setupExpired}
                      aside={
                        <Button size="sm" variant="outline" onClick={restart}>
                          {t.restart}
                        </Button>
                      }
                    />
                  )}
                  {throttle && (
                    <LockoutAlert
                      key="throttle"
                      until={throttle.until}
                      totalSeconds={throttle.totalSeconds}
                      onExpire={() => setThrottle(null)}
                    />
                  )}
                  {codeError && <Alert key="error" variant="destructive" title={codeError} />}
                </AnimatePresence>
              </div>

              <OtpInput
                ref={otpRef}
                value={code}
                onChange={(next) => {
                  setCode(next);
                  if (codeError) setCodeError(null);
                }}
                onComplete={(value) => void verify(value)}
                disabled={confirm.isPending || throttled || expired}
                invalid={Boolean(codeError)}
                autoFocus
                labelledBy="mfa-verify-title"
              />
              <Button
                block
                size="lg"
                loading={confirm.isPending}
                loadingText={t.verifying}
                disabled={code.length !== 6 || throttled || expired}
                onClick={() => void verify(code)}
              >
                {t.verify}
              </Button>
              <button
                type="button"
                onClick={() => setStep(0)}
                className="mx-auto flex items-center gap-1.5 rounded text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              >
                <ArrowLeft className="size-4" aria-hidden />
                {t.back}
              </button>
            </motion.section>
          )}

          {step === 2 && (
            <motion.section key="codes" {...stepMotion} aria-labelledby="mfa-codes-title" className="space-y-5">
              <div>
                <h2 id="mfa-codes-title" className="font-semibold">{t.codesTitle}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{t.codesBody}</p>
              </div>
              <Alert variant="warning" title={t.codesWarning} />

              <div className="print-area rounded-xl border bg-muted/40 p-4">
                <p className="mb-3 hidden text-sm font-semibold print:block">{t.fileHeader(env.appName, user.email)}</p>
                <ol className="grid grid-cols-2 gap-x-6 gap-y-2 font-mono text-[15px] tracking-wider sm:grid-cols-2">
                  {backupCodes.map((backupCode, index) => (
                    <motion.li
                      key={backupCode}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 0.05 * index }}
                      className="flex items-baseline gap-2"
                    >
                      <span className="w-5 text-right text-xs text-muted-foreground">{index + 1}.</span>
                      {backupCode}
                    </motion.li>
                  ))}
                </ol>
              </div>

              <div className="grid gap-2 sm:grid-cols-3">
                <Button variant="outline" onClick={() => void copy(backupCodes.join('\n'))}>
                  <Copy className="size-4" aria-hidden />
                  {t.copyAll}
                </Button>
                <Button variant="outline" onClick={() => downloadText('backup-codes.txt', codesText())}>
                  <Download className="size-4" aria-hidden />
                  {t.download}
                </Button>
                <Button variant="outline" onClick={print}>
                  <Printer className="size-4" aria-hidden />
                  {t.print}
                </Button>
              </div>

              <div className="flex items-start gap-3 rounded-lg border p-3">
                <Checkbox
                  id="mfa-codes-saved"
                  checked={saved}
                  onCheckedChange={(value) => setSaved(value === true)}
                  className="mt-0.5"
                />
                <label htmlFor="mfa-codes-saved" className="text-sm font-medium leading-snug">
                  {t.savedConfirm}
                </label>
              </div>

              <Button
                block
                size="lg"
                disabled={!saved}
                loading={finishing}
                loadingText={t.finishing}
                onClick={() => void finish()}
              >
                {!saved && <AlertTriangle className="size-4" aria-hidden />}
                {t.finish}
              </Button>
            </motion.section>
          )}
        </AnimatePresence>
      </motion.div>
    </AuthLayout>
  );
}
