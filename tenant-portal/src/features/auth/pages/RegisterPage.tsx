import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowRight, MailQuestion } from 'lucide-react';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { Link } from 'react-router-dom';
import { paths } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem, useShake } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import { AnimatedCheck } from '@/components/ui/AnimatedCheck';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Divider, Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { PasswordStrength } from '@/components/ui/PasswordStrength';
import { messages } from '@/i18n';
import { env } from '@/lib/env';
import { toApiError, userMessage } from '@/lib/errors';
import { applyServerErrors } from '@/lib/forms';
import { startCooldown } from '@/lib/hooks/useCooldown';
import { cn } from '@/lib/utils';
import { registerRequest } from '../api/registration';
import { GoogleButton } from '../components/GoogleButton';
import { verifyCooldownKey, VerifyEmailModal } from '../components/VerifyEmailModal';
import { LockoutAlert } from '../components/LockoutAlert';
import { registerSchema, type RegisterInput, type RegisterValues } from '../schemas';

const t = messages.register;

type FormProblem =
  | { kind: 'throttled'; until: number | null; totalSeconds: number }
  | { kind: 'message'; text: string };

const SERVER_FIELDS = ['companyName', 'firstName', 'lastName', 'email', 'password'] as const;

export function RegisterPage() {
  const [submittedEmail, setSubmittedEmail] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [problem, setProblem] = useState<FormProblem | null>(null);
  const { controls, shake } = useShake();

  const {
    register,
    control,
    handleSubmit,
    watch,
    setError,
    setFocus,
    formState: { errors },
  } = useForm<RegisterInput, unknown, RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: {
      companyName: '',
      firstName: '',
      lastName: '',
      email: '',
      password: '',
      confirmPassword: '',
      acceptTerms: false,
    },
    mode: 'onTouched',
  });

  const password = watch('password');
  const email = watch('email');

  const signUp = useMutation({ mutationFn: registerRequest });

  const onSubmit = handleSubmit(
    async (values) => {
      setProblem(null);
      try {
        await signUp.mutateAsync({
          companyName: values.companyName,
          firstName: values.firstName,
          lastName: values.lastName,
          email: values.email,
          password: values.password,
        });
        // Same screen whether or not the address was already registered (anti-enumeration).
        startCooldown(verifyCooldownKey(values.email));
        setSubmittedEmail(values.email);
        setModalOpen(true);
      } catch (error) {
        const apiError = toApiError(error);
        shake();
        if (apiError.kind === 'rate_limited') {
          const seconds = apiError.retryAfterSeconds;
          setProblem({
            kind: 'throttled',
            until: seconds ? Date.now() + seconds * 1000 : null,
            totalSeconds: seconds ?? 0,
          });
          return;
        }
        if (apiError.kind === 'validation') {
          const leftover = applyServerErrors(apiError, setError, {
            fields: SERVER_FIELDS,
            aliases: { company_name: 'companyName', first_name: 'firstName', last_name: 'lastName' },
          });
          if (leftover) setProblem({ kind: 'message', text: leftover });
          return;
        }
        setProblem({ kind: 'message', text: userMessage(apiError) });
      }
    },
    () => shake(),
  );

  const changeEmail = () => {
    setModalOpen(false);
    setSubmittedEmail(null);
    window.setTimeout(() => setFocus('email'), 350);
  };

  if (submittedEmail) {
    return (
      <AuthLayout
        icon={<AnimatedCheck size={64} />}
        title={t.successTitle}
        description={t.successBody}
      >
        <StaggerItem className="space-y-3">
          <Button block size="lg" onClick={() => setModalOpen(true)}>
            <MailQuestion className="size-4" aria-hidden />
            {t.openModal}
          </Button>
          <Button asChild block size="lg" variant="outline">
            <Link to={paths.login}>{t.backToLogin}</Link>
          </Button>
        </StaggerItem>
        <VerifyEmailModal
          open={modalOpen}
          onOpenChange={setModalOpen}
          email={submittedEmail}
          onChangeEmail={changeEmail}
        />
      </AuthLayout>
    );
  }

  const throttled = problem?.kind === 'throttled' && problem.until !== null;

  return (
    <AuthLayout
      title={t.title}
      description={t.description}
      footer={
        <>
          {t.haveAccount}{' '}
          <Link to={paths.login} className="font-semibold text-primary hover:underline">
            {t.signIn}
          </Link>
        </>
      }
    >
      <motion.div animate={controls}>
        <div className="space-y-3 empty:hidden">
          <AnimatePresence mode="popLayout">
            {problem?.kind === 'throttled' && (
              <LockoutAlert
                key="throttled"
                title={t.throttledTitle}
                body={messages.errors.rateLimited}
                until={problem.until}
                totalSeconds={problem.totalSeconds}
                onExpire={() => setProblem(null)}
              />
            )}
            {problem?.kind === 'message' && <Alert key="message" variant="destructive" title={problem.text} />}
          </AnimatePresence>
        </div>

        <StaggerItem className="mt-5">
          <GoogleButton label={messages.google.signUp} />
        </StaggerItem>
        <Divider>{messages.google.or}</Divider>

        <form onSubmit={onSubmit} noValidate className="space-y-5" aria-labelledby="page-title">
          <StaggerItem>
            <Field id="register-company" label={t.companyName} error={errors.companyName?.message}>
              {(field) => (
                <Input
                  {...field}
                  {...register('companyName')}
                  autoComplete="organization"
                  placeholder={t.companyPlaceholder}
                  autoFocus
                />
              )}
            </Field>
          </StaggerItem>

          <StaggerItem className="grid grid-cols-2 gap-3">
            <Field id="register-first-name" label={t.firstName} error={errors.firstName?.message}>
              {(field) => <Input {...field} {...register('firstName')} autoComplete="given-name" />}
            </Field>
            <Field id="register-last-name" label={t.lastName} error={errors.lastName?.message}>
              {(field) => <Input {...field} {...register('lastName')} autoComplete="family-name" />}
            </Field>
          </StaggerItem>

          <StaggerItem>
            <Field id="register-email" label={t.email} error={errors.email?.message}>
              {(field) => (
                <Input
                  {...field}
                  {...register('email')}
                  type="email"
                  autoComplete="email"
                  inputMode="email"
                  placeholder={messages.fields.emailPlaceholder}
                />
              )}
            </Field>
          </StaggerItem>

          <StaggerItem className="space-y-3">
            <Field id="register-password" label={t.password} error={errors.password?.message}>
              {(field) => (
                <PasswordInput
                  {...field}
                  aria-describedby={[field['aria-describedby'], 'register-password-policy'].filter(Boolean).join(' ')}
                  {...register('password')}
                  autoComplete="new-password"
                />
              )}
            </Field>
            <PasswordStrength id="register-password-policy" password={password} email={email} />
          </StaggerItem>

          <StaggerItem>
            <Field id="register-confirm" label={t.confirmPassword} error={errors.confirmPassword?.message}>
              {(field) => <PasswordInput {...field} {...register('confirmPassword')} autoComplete="new-password" />}
            </Field>
          </StaggerItem>

          <StaggerItem className="space-y-1.5">
            <div className="flex items-start gap-3">
              <Controller
                control={control}
                name="acceptTerms"
                render={({ field }) => (
                  <Checkbox
                    id="register-terms"
                    ref={field.ref}
                    checked={field.value}
                    onCheckedChange={(value) => field.onChange(value === true)}
                    onBlur={field.onBlur}
                    aria-invalid={Boolean(errors.acceptTerms)}
                    aria-describedby={errors.acceptTerms ? 'register-terms-error' : undefined}
                    className="mt-0.5"
                  />
                )}
              />
              <label htmlFor="register-terms" className="text-sm leading-snug text-muted-foreground">
                {env.termsUrl ? (
                  <>
                    {t.acceptTerms}{' '}
                    <a
                      href={env.termsUrl}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="font-medium text-primary hover:underline"
                    >
                      {t.terms}
                    </a>
                  </>
                ) : (
                  t.acceptTermsPlain
                )}
              </label>
            </div>
            <AnimatePresence initial={false}>
              {errors.acceptTerms && (
                <motion.p
                  id="register-terms-error"
                  role="alert"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className={cn('overflow-hidden pl-8 text-[13px] font-medium text-destructive')}
                >
                  {errors.acceptTerms.message}
                </motion.p>
              )}
            </AnimatePresence>
          </StaggerItem>

          <StaggerItem>
            <Button
              type="submit"
              block
              size="lg"
              loading={signUp.isPending}
              loadingText={t.submitting}
              disabled={throttled}
            >
              {t.submit}
              <ArrowRight className="size-4" aria-hidden />
            </Button>
          </StaggerItem>
        </form>
      </motion.div>
    </AuthLayout>
  );
}
