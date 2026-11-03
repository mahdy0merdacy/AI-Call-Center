import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { KeyRound, Link2Off, TimerOff } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { paths, type LoginLocationState } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem, useShake } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import { AnimatedCheck } from '@/components/ui/AnimatedCheck';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { PasswordStrength } from '@/components/ui/PasswordStrength';
import { messages } from '@/i18n';
import { broadcastSignOut } from '@/lib/auth/authEvents';
import { tokenStore } from '@/lib/auth/tokenStore';
import { toApiError, userMessage } from '@/lib/errors';
import { applyServerErrors } from '@/lib/forms';
import { useStripQueryOnMount } from '@/lib/hooks/useStripQueryOnMount';
import { confirmPasswordReset, resetLinkProblem, type ResetLinkProblem } from '../api/passwordReset';
import { newPasswordSchema, type NewPasswordValues } from '../schemas';

const t = messages.resetPassword;

type View = { kind: 'form' } | { kind: 'success' } | { kind: 'problem'; problem: ResetLinkProblem };

const problemCopy: Record<ResetLinkProblem, string> = {
  expired: t.expiredBody,
  used: t.usedBody,
  invalid: t.invalidBody,
};

export function ResetPasswordPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { controls, shake } = useShake();

  // Read the link once, then strip it from the address bar (single-use token).
  const [link] = useState(() => {
    const params = new URLSearchParams(location.search);
    return { uid: params.get('uid'), token: params.get('token') };
  });
  const [view, setView] = useState<View>(
    link.uid && link.token ? { kind: 'form' } : { kind: 'problem', problem: 'invalid' },
  );
  const [formError, setFormError] = useState<string | null>(null);

  useStripQueryOnMount();

  const {
    register,
    handleSubmit,
    watch,
    setError,
    formState: { errors },
  } = useForm<NewPasswordValues>({
    resolver: zodResolver(newPasswordSchema),
    defaultValues: { password: '', confirmPassword: '' },
    mode: 'onTouched',
  });
  const password = watch('password');

  const confirm = useMutation({
    mutationFn: (newPassword: string) => {
      if (!link.uid || !link.token) throw new Error('Missing reset link');
      return confirmPasswordReset(link.uid, link.token, newPassword);
    },
  });

  const onSubmit = handleSubmit(
    async ({ password: newPassword }) => {
      setFormError(null);
      try {
        await confirm.mutateAsync(newPassword);
        // The backend revoked every session: drop ours too, here and in other tabs.
        tokenStore.clear();
        broadcastSignOut();
        setView({ kind: 'success' });
      } catch (error) {
        const problem = resetLinkProblem(error);
        if (problem) {
          setView({ kind: 'problem', problem });
          return;
        }
        shake();
        const apiError = toApiError(error);
        if (apiError.kind === 'validation') {
          const leftover = applyServerErrors(apiError, setError, {
            fields: ['password', 'confirmPassword'],
            aliases: { new_password: 'password' },
          });
          if (leftover) setFormError(leftover);
          return;
        }
        setFormError(userMessage(apiError));
      }
    },
    () => shake(),
  );

  if (view.kind === 'success') {
    const state: LoginLocationState = { notice: 'password_reset' };
    return (
      <AuthLayout icon={<AnimatedCheck size={64} />} title={t.successTitle} description={t.successBody}>
        <StaggerItem>
          <Button block size="lg" onClick={() => navigate(paths.login, { replace: true, state })}>
            {t.signIn}
          </Button>
        </StaggerItem>
      </AuthLayout>
    );
  }

  if (view.kind === 'problem') {
    const Icon = view.problem === 'expired' ? TimerOff : Link2Off;
    return (
      <AuthLayout
        icon={
          <span className="flex size-12 items-center justify-center rounded-xl bg-warning/15 text-warning">
            <Icon className="size-6" aria-hidden />
          </span>
        }
        title={t.invalidTitle}
        description={problemCopy[view.problem]}
      >
        <StaggerItem className="space-y-3">
          <Button asChild block size="lg">
            <Link to={paths.forgotPassword}>{t.requestNew}</Link>
          </Button>
          <Button asChild block size="lg" variant="ghost">
            <Link to={paths.login}>{messages.forgotPassword.backToLogin}</Link>
          </Button>
        </StaggerItem>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      icon={
        <span className="flex size-12 items-center justify-center rounded-xl bg-accent text-accent-foreground">
          <KeyRound className="size-6" aria-hidden />
        </span>
      }
      title={t.title}
      description={t.description}
    >
      <motion.div animate={controls}>
        <AnimatePresence>
          {formError && <Alert key="error" variant="destructive" title={formError} className="mb-5" />}
        </AnimatePresence>
        <form onSubmit={onSubmit} noValidate className="space-y-5" aria-labelledby="page-title">
          <StaggerItem className="space-y-3">
            <Field id="reset-password" label={t.password} error={errors.password?.message}>
              {(control) => (
                <PasswordInput
                  {...control}
                  aria-describedby={[control['aria-describedby'], 'reset-password-policy'].filter(Boolean).join(' ')}
                  {...register('password')}
                  autoComplete="new-password"
                  autoFocus
                />
              )}
            </Field>
            <PasswordStrength id="reset-password-policy" password={password} />
          </StaggerItem>
          <StaggerItem>
            <Field id="reset-confirm" label={t.confirmPassword} error={errors.confirmPassword?.message}>
              {(control) => <PasswordInput {...control} {...register('confirmPassword')} autoComplete="new-password" />}
            </Field>
          </StaggerItem>
          <StaggerItem>
            <Button type="submit" block size="lg" loading={confirm.isPending} loadingText={t.submitting}>
              {t.submit}
            </Button>
          </StaggerItem>
        </form>
      </motion.div>
    </AuthLayout>
  );
}
