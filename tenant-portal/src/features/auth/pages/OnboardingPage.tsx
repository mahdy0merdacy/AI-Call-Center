import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { Building2, Globe } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { useAuthedUser } from '@/app/guards';
import { paths } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem, useShake } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { messages } from '@/i18n';
import { toApiError, userMessage } from '@/lib/errors';
import { applyServerErrors } from '@/lib/forms';
import { slugify } from '@/lib/slug';
import { onboardingRequest } from '../api/onboarding';
import { authKeys } from '../queryKeys';
import { useSession } from '../session/SessionProvider';

const t = messages.onboarding;
const rv = messages.registerValidation;

const onboardingSchema = z.object({
  companyName: z.string().trim().min(1, rv.companyRequired).max(100, rv.companyTooLong),
});
type OnboardingValues = z.infer<typeof onboardingSchema>;

export function OnboardingPage() {
  const user = useAuthedUser();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { establishSession, signOut } = useSession();
  const { controls, shake } = useShake();
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    watch,
    setError,
    formState: { errors },
  } = useForm<OnboardingValues>({
    resolver: zodResolver(onboardingSchema),
    defaultValues: { companyName: '' },
  });
  const slug = slugify(watch('companyName'));

  const create = useMutation({ mutationFn: onboardingRequest });

  const onSubmit = handleSubmit(
    async ({ companyName }) => {
      setFormError(null);
      try {
        const accessToken = await create.mutateAsync(companyName);
        // New tokens carry tenant_id; this also refetches /auth/me/.
        establishSession(accessToken);
        navigate(paths.dashboard, { replace: true });
      } catch (error) {
        const apiError = toApiError(error);
        if (apiError.status === 409 || apiError.hasCode('tenant_already_exists')) {
          // Created in another tab meanwhile: reload the user and move on.
          await queryClient.invalidateQueries({ queryKey: authKeys.me() });
          navigate(paths.dashboard, { replace: true });
          return;
        }
        shake();
        if (apiError.kind === 'validation') {
          const leftover = applyServerErrors(apiError, setError, {
            fields: ['companyName'],
            aliases: { company_name: 'companyName' },
          });
          if (leftover) setFormError(leftover);
          return;
        }
        setFormError(userMessage(apiError));
      }
    },
    () => shake(),
  );

  return (
    <AuthLayout
      icon={
        <span className="flex size-12 items-center justify-center rounded-xl bg-accent text-accent-foreground">
          <Building2 className="size-6" aria-hidden />
        </span>
      }
      title={t.title}
      description={t.description}
      footer={
        <span className="flex flex-col items-center gap-1">
          <span>{t.signedInAs(user.email)}</span>
          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            {t.notYou}
          </button>
        </span>
      }
    >
      <motion.div animate={controls}>
        <AnimatePresence>
          {formError && <Alert key="error" variant="destructive" title={formError} className="mb-5" />}
        </AnimatePresence>
        <form onSubmit={onSubmit} noValidate className="space-y-5" aria-labelledby="page-title">
          <StaggerItem>
            <Field
              id="onboarding-company"
              label={t.companyName}
              error={errors.companyName?.message}
              hint={
                <span className="flex items-center gap-1.5">
                  <Globe className="size-3.5 shrink-0" aria-hidden />
                  <span className="sr-only">{t.slugLabel}: </span>
                  <span className="truncate font-mono text-xs">
                    {window.location.host}/w/
                    <motion.span key={slug} initial={{ opacity: 0.4 }} animate={{ opacity: 1 }} className="text-foreground">
                      {slug || '…'}
                    </motion.span>
                  </span>
                </span>
              }
            >
              {(control) => (
                <Input
                  {...control}
                  {...register('companyName')}
                  autoComplete="organization"
                  placeholder={t.companyPlaceholder}
                  autoFocus
                />
              )}
            </Field>
            <p className="mt-1.5 text-xs text-muted-foreground">{t.slugHint}</p>
          </StaggerItem>
          <StaggerItem>
            <Button type="submit" block size="lg" loading={create.isPending} loadingText={t.submitting}>
              {t.submit}
            </Button>
          </StaggerItem>
        </form>
      </motion.div>
    </AuthLayout>
  );
}
