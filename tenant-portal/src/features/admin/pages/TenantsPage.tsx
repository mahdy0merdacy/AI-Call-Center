import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem } from '@/components/motion';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { FullPageError } from '@/components/feedback/FullPageError';
import { Spinner } from '@/components/ui/spinner';
import { messages } from '@/i18n';
import { env } from '@/lib/env';
import { listTenants, suspendTenant, unsuspendTenant, type TenantAdmin } from '../api';

const t = messages.adminTenants;

const tenantsKey = ['admin', 'tenants'] as const;

function statusBadge(active: boolean) {
  return (
    <Badge variant={active ? 'success' : 'warning'}>{active ? t.active : t.suspended}</Badge>
  );
}

function FormattedDate({ value }: { value: string | null }): React.ReactNode {
  if (!value) return <span>—</span>;
  const date = new Date(value);
  return (
    <time dateTime={value}>
      {Number.isNaN(date.getTime())
        ? value
        : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}
    </time>
  );
}

export function TenantsPage() {
  const [toSuspend, setToSuspend] = useState<TenantAdmin | null>(null);
  const queryClient = useQueryClient();

  const list = useQuery({ queryKey: tenantsKey, queryFn: listTenants });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: tenantsKey });

  const suspend = useMutation({
    mutationFn: (id: string) => suspendTenant(id),
    onSuccess: async (tenant) => {
      toast.success(t.suspendedToast(tenant.name));
      setToSuspend(null);
      await invalidate();
    },
  });

  const unsuspend = useMutation({
    mutationFn: (id: string) => unsuspendTenant(id),
    onSuccess: async (tenant) => {
      toast.success(t.reactivatedToast(tenant.name));
      await invalidate();
    },
  });

  if (list.isPending) {
    return (
      <AuthLayout
        icon={<Spinner label={messages.common.loading} />}
        title={t.title}
        description={t.description}
      >
        <div />
      </AuthLayout>
    );
  }

  if (list.isError) {
    return (
      <AuthLayout title={t.title} description={t.description}>
        <FullPageError
          title={t.title}
          error={list.error}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
        />
      </AuthLayout>
    );
  }

  const tenants = list.data;
  const busy = suspend.isPending;

  return (
    <AuthLayout
      icon={<Building2 aria-hidden />}
      title={t.title}
      description={t.description}
      wide
      documentTitle={`${t.title} · ${env.appName}`}
    >
      <StaggerItem>
        {tenants.length === 0 ? (
          <div className="rounded-xl border border-dashed p-8 text-center">
            <p className="font-medium">{t.empty}</p>
            <p className="text-sm text-muted-foreground">{t.emptyBody}</p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border bg-card">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th scope="col" className="px-4 py-3 font-medium">{t.colName}</th>
                  <th scope="col" className="px-4 py-3 font-medium">{t.colSlug}</th>
                  <th scope="col" className="px-4 py-3 font-medium">{t.colStatus}</th>
                  <th scope="col" className="px-4 py-3 font-medium">{t.colCreated}</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">{t.actions}</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((tenant) => (
                  <tr key={tenant.id} className="border-b last:border-0">
                    <td className="px-4 py-3 font-medium">{tenant.name}</td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{tenant.slug}</td>
                    <td className="px-4 py-3">{statusBadge(tenant.isActive)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted-foreground">
                      <FormattedDate value={tenant.createdAt} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      {tenant.isActive ? (
                        <Button size="sm" variant="destructive" disabled={busy} onClick={() => setToSuspend(tenant)}>
                          {t.suspend}
                        </Button>
                      ) : (
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => unsuspend.mutate(tenant.id)}>
                          {t.unsuspend}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </StaggerItem>

      <SuspendConfirmDialog
        tenant={toSuspend}
        pending={suspend.isPending}
        onCancel={() => setToSuspend(null)}
        onConfirm={() => toSuspend && suspend.mutate(toSuspend.id)}
      />
    </AuthLayout>
  );
}

function SuspendConfirmDialog({
  tenant,
  pending,
  onCancel,
  onConfirm,
}: {
  tenant: TenantAdmin | null;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog open={tenant !== null} onOpenChange={(open) => !open && onCancel()}>
      <AlertDialogContent>
        <AlertDialogTitle>{tenant ? t.suspendTitle(tenant.name) : ''}</AlertDialogTitle>
        <AlertDialogDescription>{t.suspendBody}</AlertDialogDescription>
        <div className="mt-5 flex justify-end gap-2">
          <AlertDialogCancel asChild>
            <Button variant="outline" disabled={pending}>
              {t.cancel}
            </Button>
          </AlertDialogCancel>
          <Button variant="destructive" disabled={pending} onClick={onConfirm}>
            {pending ? <Spinner className="size-4" /> : t.confirmSuspend}
          </Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
