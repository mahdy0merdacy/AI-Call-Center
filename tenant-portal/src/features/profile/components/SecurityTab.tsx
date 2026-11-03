import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { ExternalLink, LogOut, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { useAuthedUser } from '@/app/guards';
import { useShake } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field } from '@/components/ui/field';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { PasswordStrength } from '@/components/ui/PasswordStrength';
import { LockoutAlert } from '@/features/auth/components/LockoutAlert';
import { useSession } from '@/features/auth/session/SessionProvider';
import { messages } from '@/i18n';
import { tokenStore } from '@/lib/auth/tokenStore';
import { toApiError, userMessage } from '@/lib/errors';
import { applyServerErrors } from '@/lib/forms';
import { changePassword } from '../api';
import { changePasswordSchema, type ChangePasswordValues } from '../schemas';

const t = messages.profile;

type Feedback =
  | { kind: 'success' }
  | { kind: 'error'; text: string }
  | { kind: 'throttled'; until: number | null; totalSeconds: number };

function ChangePasswordCard() {
  const user = useAuthedUser();
  const { controls, shake } = useShake();
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const {
    register,
    handleSubmit,
    watch,
    reset,
    setError,
    formState: { errors },
  } = useForm<ChangePasswordValues>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { currentPassword: '', password: '', confirmPassword: '' },
    mode: 'onTouched',
  });
  const password = watch('password');

  const mutation = useMutation({
    mutationFn: ({ currentPassword, password: next }: ChangePasswordValues) => changePassword(currentPassword, next),
  });

  const onSubmit = handleSubmit(
    async (values) => {
      setFeedback(null);
      try {
        const accessToken = await mutation.mutateAsync(values);
        if (accessToken) tokenStore.set(accessToken);
        reset();
        setFeedback({ kind: 'success' });
        toast.success(t.changed);
      } catch (error) {
        shake();
        const apiError = toApiError(error);
        if (apiError.kind === 'rate_limited') {
          const seconds = apiError.retryAfterSeconds;
          setFeedback({ kind: 'throttled', until: seconds ? Date.now() + seconds * 1000 : null, totalSeconds: seconds ?? 0 });
          return;
        }
        if (apiError.hasCode('invalid_password', 'wrong_password')) {
          setError('currentPassword', { type: 'server', message: t.wrongCurrent }, { shouldFocus: true });
          return;
        }
        if (apiError.kind === 'validation') {
          const leftover = applyServerErrors(apiError, setError, {
            fields: ['currentPassword', 'password', 'confirmPassword'],
            aliases: { current_password: 'currentPassword', new_password: 'password' },
          });
          if (leftover) setFeedback({ kind: 'error', text: leftover });
          return;
        }
        setFeedback({ kind: 'error', text: userMessage(apiError) });
      }
    },
    () => shake(),
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t.passwordCard}</CardTitle>
        <CardDescription>{t.passwordCardBody}</CardDescription>
      </CardHeader>
      <CardContent>
        <motion.div animate={controls} className="space-y-5">
          <AnimatePresence mode="popLayout">
            {feedback?.kind === 'success' && (
              <Alert key="ok" variant="success" title={t.changed}>
                {t.changedBody}
              </Alert>
            )}
            {feedback?.kind === 'error' && <Alert key="error" variant="destructive" title={feedback.text} />}
            {feedback?.kind === 'throttled' && (
              <LockoutAlert
                key="throttled"
                body={messages.errors.rateLimited}
                until={feedback.until}
                totalSeconds={feedback.totalSeconds}
                onExpire={() => setFeedback(null)}
              />
            )}
          </AnimatePresence>

          <form onSubmit={onSubmit} noValidate className="space-y-5">
            {/* Lets password managers attach the change to the right account. */}
            <input type="text" name="username" autoComplete="username" value={user.email} readOnly hidden />
            <Field id="change-current" label={t.currentPassword} error={errors.currentPassword?.message}>
              {(control) => (
                <PasswordInput {...control} {...register('currentPassword')} autoComplete="current-password" />
              )}
            </Field>
            <div className="space-y-3">
              <Field id="change-new" label={t.newPassword} error={errors.password?.message}>
                {(control) => (
                  <PasswordInput
                    {...control}
                    aria-describedby={[control['aria-describedby'], 'change-password-policy'].filter(Boolean).join(' ')}
                    {...register('password')}
                    autoComplete="new-password"
                  />
                )}
              </Field>
              <PasswordStrength id="change-password-policy" password={password} email={user.email} />
            </div>
            <Field id="change-confirm" label={t.confirmPassword} error={errors.confirmPassword?.message}>
              {(control) => <PasswordInput {...control} {...register('confirmPassword')} autoComplete="new-password" />}
            </Field>
            <Button type="submit" loading={mutation.isPending} loadingText={t.changing}>
              {t.changePassword}
            </Button>
          </form>
        </motion.div>
      </CardContent>
    </Card>
  );
}

function GoogleAccountCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t.googleCardTitle}</CardTitle>
        <CardDescription>{t.googleCardBody}</CardDescription>
      </CardHeader>
      <CardContent>
        <Button asChild variant="outline">
          <a href="https://myaccount.google.com/security" target="_blank" rel="noreferrer noopener">
            {t.googleCardLink}
            <ExternalLink className="size-4" aria-hidden />
          </a>
        </Button>
      </CardContent>
    </Card>
  );
}

function MfaCard() {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1.5">
          <CardTitle>{t.mfaCard}</CardTitle>
          <CardDescription>{t.mfaOnBody}</CardDescription>
          <p className="text-xs text-muted-foreground">{t.mfaRequiredNote}</p>
        </div>
        <Badge variant="success" className="shrink-0">
          <ShieldCheck aria-hidden />
          {t.mfaOn}
        </Badge>
      </CardHeader>
    </Card>
  );
}

function SessionCard() {
  const { signOut } = useSession();
  const [signingOut, setSigningOut] = useState(false);

  return (
    <Card className="border-destructive/25">
      <CardHeader className="flex-col gap-4 space-y-0 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1.5">
          <CardTitle>{t.sessionCard}</CardTitle>
          <CardDescription>{t.sessionBody}</CardDescription>
        </div>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="outline" className="shrink-0 border-destructive/40 text-destructive hover:bg-destructive/10">
              <LogOut className="size-4" aria-hidden />
              {t.signOut}
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogTitle>{t.confirmTitle}</AlertDialogTitle>
            <AlertDialogDescription>{t.confirmBody}</AlertDialogDescription>
            <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <AlertDialogCancel>{t.confirmCancel}</AlertDialogCancel>
              <AlertDialogAction
                className={buttonVariants({ variant: 'destructive' })}
                disabled={signingOut}
                onClick={(event) => {
                  // Keep the dialog open while the request runs.
                  event.preventDefault();
                  setSigningOut(true);
                  void signOut();
                }}
              >
                {t.confirmAction}
              </AlertDialogAction>
            </div>
          </AlertDialogContent>
        </AlertDialog>
      </CardHeader>
    </Card>
  );
}

export function SecurityTab() {
  const user = useAuthedUser();
  return (
    <div className="space-y-6">
      {user.auth_provider === 'google' ? <GoogleAccountCard /> : <ChangePasswordCard />}
      {user.role === 'platform_admin' && <MfaCard />}
      <SessionCard />
    </div>
  );
}
