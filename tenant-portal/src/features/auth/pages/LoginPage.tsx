import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowRight } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { paths, type LoginLocationState } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem, useShake } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Divider, Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { messages } from '@/i18n';
import { classifyLoginError, loginRequest, type LoginFailure } from '../api/login';
import { GoogleButton } from '../components/GoogleButton';
import { LockoutAlert } from '../components/LockoutAlert';
import { LoginNoticeBanner } from '../components/LoginNoticeBanner';
import { VerifyEmailModal } from '../components/VerifyEmailModal';
import { mfaChallengeStore } from '../mfa/challengeStore';
import { emailField, loginSchema, type LoginValues } from '../schemas';
import { useSession } from '../session/SessionProvider';

const t = messages.login;

interface Lock {
  until: number | null;
  totalSeconds: number;
}

export function LoginPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const routerState = (location.state ?? null) as LoginLocationState | null;
  const { establishSession } = useSession();
  const { controls, shake } = useShake();

  const [notice, setNotice] = useState(routerState?.notice);
  const [failure, setFailure] = useState<LoginFailure | null>(null);
  const [lock, setLock] = useState<Lock | null>(null);
  const [resendOpen, setResendOpen] = useState(false);

  const {
    register,
    handleSubmit,
    resetField,
    setFocus,
    getValues,
    formState: { errors },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  });

  const login = useMutation({
    mutationFn: ({ email, password }: LoginValues) => loginRequest(email, password),
  });

  const onSubmit = handleSubmit(
    async (values) => {
      setNotice(undefined);
      setFailure(null);
      try {
        const result = await login.mutateAsync(values);
        if (result.kind === 'mfa_required') {
          mfaChallengeStore.start(result.mfaToken, result.expiresInSeconds, routerState?.from);
          const state: LoginLocationState = { from: routerState?.from };
          navigate(paths.loginMfa, { state });
          return;
        }
        // <PublicOnly> sees the new session and redirects to `from` or the dashboard.
        establishSession(result.accessToken);
      } catch (error) {
        const classified = classifyLoginError(error);
        setFailure(classified);
        shake();
        if (classified.kind === 'locked') {
          const seconds = classified.retryAfterSeconds;
          setLock({ until: seconds ? Date.now() + seconds * 1000 : null, totalSeconds: seconds ?? 0 });
        } else if (classified.kind === 'invalid') {
          resetField('password');
          setFocus('password');
        }
      }
    },
    () => shake(),
  );

  const locked = lock !== null && lock.until !== null;

  return (
    <AuthLayout
      title={t.title}
      description={t.description}
      footer={
        <>
          {t.noAccount}{' '}
          <Link to={paths.register} className="font-semibold text-primary hover:underline">
            {t.createAccount}
          </Link>
        </>
      }
    >
      <motion.div animate={controls}>
        <div className="space-y-3 empty:hidden" aria-live="polite">
          <AnimatePresence mode="popLayout">
            {notice && !failure && <LoginNoticeBanner key={`notice-${notice}`} notice={notice} />}
            {failure?.kind === 'invalid' && (
              <Alert key="invalid" variant="destructive" title={t.invalidTitle}>
                {t.invalidBody}{' '}
                {/* Shown for every failure, so it reveals nothing about the account. */}
                <span className="mt-1 block">
                  {t.resendPrompt}{' '}
                  <button
                    type="button"
                    onClick={() => setResendOpen(true)}
                    className="rounded font-semibold text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                  >
                    {t.resendLink}
                  </button>
                </span>
              </Alert>
            )}
            {failure?.kind === 'locked' && lock && (
              <LockoutAlert
                key="locked"
                until={lock.until}
                totalSeconds={lock.totalSeconds}
                onExpire={() => {
                  setLock(null);
                  setFailure(null);
                }}
              />
            )}
            {(failure?.kind === 'network' || failure?.kind === 'server') && (
              <Alert key="network" variant="destructive" title={failure.message} />
            )}
          </AnimatePresence>
        </div>

        <StaggerItem className="mt-5">
          <GoogleButton returnTo={routerState?.from} />
        </StaggerItem>
        <Divider>{messages.google.or}</Divider>

        <form onSubmit={onSubmit} noValidate className="space-y-5" aria-labelledby="page-title">
          <StaggerItem>
            <Field id="login-email" label={messages.fields.email} error={errors.email?.message}>
              {(control) => (
                <Input
                  {...control}
                  {...register('email')}
                  type="email"
                  autoComplete="username"
                  inputMode="email"
                  placeholder={messages.fields.emailPlaceholder}
                  autoFocus
                />
              )}
            </Field>
          </StaggerItem>

          <StaggerItem>
            <Field
              id="login-password"
              label={messages.fields.password}
              error={errors.password?.message}
              labelAside={
                <Link
                  to={paths.forgotPassword}
                  className="text-[13px] font-medium text-primary hover:underline"
                >
                  {t.forgotPassword}
                </Link>
              }
            >
              {(control) => (
                <PasswordInput {...control} {...register('password')} autoComplete="current-password" />
              )}
            </Field>
          </StaggerItem>

          <StaggerItem>
            <Button
              type="submit"
              block
              size="lg"
              loading={login.isPending}
              loadingText={t.submitting}
              disabled={locked}
            >
              {t.submit}
              <ArrowRight className="size-4" aria-hidden />
            </Button>
          </StaggerItem>
        </form>
      </motion.div>
      <VerifyEmailModal
        open={resendOpen}
        onOpenChange={setResendOpen}
        email={emailField.safeParse(getValues('email')).success ? getValues('email').trim().toLowerCase() : null}
      />
    </AuthLayout>
  );
}
