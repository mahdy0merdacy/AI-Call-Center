import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { BadgeCheck, CircleAlert, KeyRound, ShieldCheck, ShieldOff } from 'lucide-react';
import type { ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { useAuthedUser } from '@/app/guards';
import { Avatar } from '@/components/layout/AppHeader';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useCurrentTenant } from '@/features/auth/hooks/useCurrentUser';
import { authKeys } from '@/features/auth/queryKeys';
import type { CurrentUser } from '@/features/auth/types';
import { locale, messages } from '@/i18n';
import { toApiError, userMessage } from '@/lib/errors';
import { applyServerErrors } from '@/lib/forms';
import { updateProfile } from '../api';
import { profileSchema, type ProfileValues } from '../schemas';

const t = messages.profile;

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 py-3 sm:flex-row sm:items-center sm:justify-between">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium">{children}</dd>
    </div>
  );
}

function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '—'
    : new Intl.DateTimeFormat(locale === 'fr' ? 'fr-FR' : 'en-GB', { dateStyle: 'long' }).format(date);
}

export function ProfileTab() {
  const user = useAuthedUser();
  const tenant = useCurrentTenant(user.tenant_id !== null);
  const queryClient = useQueryClient();

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isDirty },
  } = useForm<ProfileValues>({
    resolver: zodResolver(profileSchema),
    defaultValues: { fullName: user.full_name ?? '' },
  });

  // Optimistic: the name changes everywhere (header avatar too) before the
  // server answers, and is rolled back if it fails.
  const update = useMutation({
    mutationFn: updateProfile,
    onMutate: async (fullName: string) => {
      await queryClient.cancelQueries({ queryKey: authKeys.me() });
      const previous = queryClient.getQueryData<CurrentUser>(authKeys.me());
      if (previous) queryClient.setQueryData<CurrentUser>(authKeys.me(), { ...previous, full_name: fullName || null });
      return { previous };
    },
    onError: (_error, _fullName, context) => {
      if (context?.previous) queryClient.setQueryData(authKeys.me(), context.previous);
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(authKeys.me(), saved);
    },
  });

  const onSubmit = handleSubmit(async ({ fullName }) => {
    try {
      const saved = await update.mutateAsync(fullName);
      reset({ fullName: saved.full_name ?? '' });
      toast.success(t.saved);
    } catch (error) {
      const apiError = toApiError(error);
      const leftover =
        apiError.kind === 'validation'
          ? applyServerErrors(apiError, setError, { fields: ['fullName'], aliases: { full_name: 'fullName' } })
          : userMessage(apiError);
      reset({ fullName }, { keepDirty: true, keepValues: true });
      if (leftover) toast.error(t.saveFailed, { description: leftover });
    }
  });

  const isGoogle = user.auth_provider === 'google';

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex-row items-center gap-4 space-y-0">
          <Avatar name={user.full_name} email={user.email} size="lg" />
          <div className="min-w-0 space-y-2">
            <div>
              <CardTitle className="truncate text-lg">{user.full_name || user.email}</CardTitle>
              <p className="truncate text-sm text-muted-foreground">{user.email}</p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="primary">{messages.roles[user.role]}</Badge>
              <Badge variant={isGoogle ? 'neutral' : 'info'}>
                {isGoogle ? <GoogleMark /> : <KeyRound aria-hidden />}
                {isGoogle ? t.providerGoogle : t.providerPassword}
              </Badge>
              <Badge variant={user.is_email_verified ? 'success' : 'warning'}>
                {user.is_email_verified ? <BadgeCheck aria-hidden /> : <CircleAlert aria-hidden />}
                {user.is_email_verified ? t.verified : t.unverified}
              </Badge>
              <Badge variant={user.mfa_enabled ? 'success' : 'neutral'}>
                {user.mfa_enabled ? <ShieldCheck aria-hidden /> : <ShieldOff aria-hidden />}
                {user.mfa_enabled ? t.mfaOn : t.mfaOff}
              </Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <Field id="profile-full-name" label={t.fullName} error={errors.fullName?.message} className="flex-1">
              {(control) => (
                <Input
                  {...control}
                  {...register('fullName')}
                  autoComplete="name"
                  placeholder={t.fullNamePlaceholder}
                />
              )}
            </Field>
            <Button type="submit" disabled={!isDirty} loading={update.isPending} loadingText={t.saving} className="sm:mb-px">
              {t.save}
            </Button>
          </form>

          <dl className="divide-y border-t">
            <Detail label={t.email}>
              <span className="flex flex-col items-start sm:items-end">
                {user.email}
                <span className="text-xs font-normal text-muted-foreground">{t.emailHint}</span>
              </span>
            </Detail>
            <Detail label={t.company}>
              {user.tenant_id === null ? (
                t.noCompany
              ) : tenant.isPending ? (
                <Skeleton className="h-4 w-32" />
              ) : (
                (tenant.data?.name ?? user.tenant_name ?? '—')
              )}
            </Detail>
            <Detail label={t.memberSince}>{formatDate(user.date_joined)}</Detail>
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
