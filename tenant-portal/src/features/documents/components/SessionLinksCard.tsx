import { Ban, Copy, Plus } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { messages } from '@/i18n';
import { copyText } from '@/lib/clipboard';
import { toApiError, userMessage } from '@/lib/errors';
import { formatDateTime } from '../format';
import { useCreateSessionLink, useDeactivateSessionLink, useSessionLinks } from '../hooks/useSessionLinks';
import { linkState, type LinkState, type SessionLink } from '../types';

const t = messages.documents.links;

const STATE_BADGE: Record<LinkState, NonNullable<BadgeProps['variant']>> = {
  active: 'success',
  expired: 'warning',
  inactive: 'neutral',
};

/** Value for `<input type="datetime-local" min>`: now, in local time, to the minute. */
function localNowValue(): string {
  const now = new Date();
  const offsetMs = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 16);
}

function CreateForm() {
  const create = useCreateSessionLink();
  const [expires, setExpires] = useState('');
  const [error, setError] = useState<string>();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    let expiresAt: string | null = null;
    if (expires) {
      const date = new Date(expires); // datetime-local is read in the browser's time zone
      if (Number.isNaN(date.getTime()) || date.getTime() <= Date.now()) {
        setError(t.expiryPast);
        return;
      }
      expiresAt = date.toISOString();
    }
    setError(undefined);
    try {
      await create.mutateAsync(expiresAt);
      setExpires('');
      toast.success(t.toastCreated);
    } catch (caught) {
      // The service reports a bad expiry under `expires_at`; anything else is a general failure.
      const fieldMessage = toApiError(caught).fieldErrors.expires_at?.[0];
      if (fieldMessage) setError(fieldMessage);
      else toast.error(t.createFailed, { description: userMessage(caught) });
    }
  }

  return (
    <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-4 sm:flex-row sm:items-end" noValidate>
      <Field id="link-expires" label={t.expiresLabel} hint={t.expiresHint} error={error} className="sm:max-w-xs sm:flex-1">
        {(control) => (
          <Input
            {...control}
            type="datetime-local"
            value={expires}
            min={localNowValue()}
            onChange={(event) => {
              setExpires(event.target.value);
              setError(undefined);
            }}
          />
        )}
      </Field>
      <Button type="submit" loading={create.isPending} loadingText={t.creating} className="sm:mb-[1.625rem]">
        <Plus className="size-4" aria-hidden />
        {t.create}
      </Button>
    </form>
  );
}

function LinkRow({ link }: { link: SessionLink }) {
  const deactivate = useDeactivateSessionLink();
  const state = linkState(link);
  const deactivating = deactivate.isPending && deactivate.variables === link.id;

  async function handleCopy() {
    if (await copyText(link.url)) toast.success(t.copied);
    else toast.error(t.copyFailed);
  }

  function handleDeactivate() {
    deactivate.mutate(link.id, {
      onSuccess: () => toast.success(t.deactivated),
      onError: (error) => toast.error(t.deactivateFailed, { description: userMessage(error) }),
    });
  }

  return (
    <li className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 space-y-1.5">
        <div className="flex items-center gap-2">
          <Badge variant={STATE_BADGE[state]}>{t.state[state]}</Badge>
          <code className="min-w-0 truncate rounded bg-muted px-2 py-1 text-xs" title={link.url}>
            {link.url}
          </code>
        </div>
        <p className="text-xs text-muted-foreground">
          {t.createdOn(formatDateTime(link.created_at))} ·{' '}
          {link.expires_at ? t.expiresOn(formatDateTime(link.expires_at)) : t.noExpiry}
        </p>
      </div>

      <div className="flex shrink-0 gap-2">
        <Button variant="outline" size="sm" onClick={() => void handleCopy()} disabled={state !== 'active'}>
          <Copy className="size-4" aria-hidden />
          {t.copy}
        </Button>
        {link.is_active && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                loading={deactivating}
                loadingText={t.deactivating}
                className="border-destructive/40 text-destructive hover:bg-destructive/10"
              >
                <Ban className="size-4" aria-hidden />
                {t.deactivate}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogTitle>{t.confirmTitle}</AlertDialogTitle>
              <AlertDialogDescription>{t.confirmBody}</AlertDialogDescription>
              <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <AlertDialogCancel>{t.confirmCancel}</AlertDialogCancel>
                <AlertDialogAction className={buttonVariants({ variant: 'destructive' })} onClick={handleDeactivate}>
                  {t.confirmAction}
                </AlertDialogAction>
              </div>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
    </li>
  );
}

export function SessionLinksCard() {
  const links = useSessionLinks();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t.title}</CardTitle>
        <CardDescription>{t.description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <CreateForm />

        {links.isPending ? (
          <div className="space-y-3" aria-busy="true" aria-label={messages.common.loading}>
            <Skeleton className="h-4 w-64" />
            <Skeleton className="h-4 w-48" />
          </div>
        ) : links.isError ? (
          <Alert
            variant="destructive"
            title={t.loadFailed}
            aside={
              <Button variant="outline" size="sm" onClick={() => void links.refetch()} loading={links.isFetching}>
                {messages.documents.retry}
              </Button>
            }
          >
            {userMessage(links.error)}
          </Alert>
        ) : links.data.results.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t.empty}</p>
        ) : (
          <ul className="divide-y border-t">
            {links.data.results.map((link) => (
              <LinkRow key={link.id} link={link} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
