/**
 * Placeholder dashboard (the real one is out of scope): proves the
 * sign-in flow end to end and offers sign-out.
 */
import { LogOut, Settings } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useAuthedUser } from '@/app/guards';
import { paths } from '@/app/paths';
import { AppHeader } from '@/components/layout/AppHeader';
import { Stagger, StaggerItem } from '@/components/motion';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { WaveformVisual } from '@/components/ui/WaveformVisual';
import { useCurrentTenant } from '@/features/auth/hooks/useCurrentUser';
import { useSession } from '@/features/auth/session/SessionProvider';
import { messages } from '@/i18n';
import { useDocumentTitle } from '@/lib/hooks/useDocumentTitle';

const t = messages.dashboard;

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 py-3 sm:flex-row sm:items-center sm:justify-between">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium">{children}</dd>
    </div>
  );
}

export function DashboardPage() {
  const user = useAuthedUser();
  const tenant = useCurrentTenant(user.tenant_id !== null);
  const { signOut } = useSession();
  const [signingOut, setSigningOut] = useState(false);

  const handleSignOut = async () => {
    setSigningOut(true);
    await signOut();
  };

  const displayName = user.full_name || user.email;
  useDocumentTitle(messages.nav.dashboard);

  return (
    <div className="min-h-dvh bg-background">
      <AppHeader />

      <main id="main" tabIndex={-1} className="mx-auto max-w-5xl px-4 py-10 focus:outline-none sm:px-6">
        <Stagger className="space-y-8">
          <StaggerItem className="mesh-bg grain relative overflow-hidden rounded-2xl p-8 text-white">
            <h1 className="text-3xl font-bold">{t.welcome(displayName)}</h1>
            <p className="mt-2 max-w-xl text-white/75">{t.description}</p>
            <div className="mt-6 h-14 opacity-90">
              <WaveformVisual bars={72} intensity={0.9} />
            </div>
          </StaggerItem>

          <StaggerItem>
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle>{t.accountTitle}</CardTitle>
                <div className="flex gap-2">
                  <Button asChild variant="ghost" size="sm">
                    <Link to={paths.profile}>
                      <Settings className="size-4" aria-hidden />
                      <span className="hidden sm:inline">{messages.nav.profile}</span>
                    </Link>
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void handleSignOut()}
                    loading={signingOut}
                    loadingText={t.signingOut}
                  >
                    <LogOut className="size-4" aria-hidden />
                    {t.signOut}
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <dl className="divide-y">
                  <Row label={t.email}>{user.email}</Row>
                  <Row label={t.role}>{messages.roles[user.role]}</Row>
                  <Row label={t.organization}>
                    {user.tenant_id === null ? (
                      t.noOrganization
                    ) : tenant.isPending ? (
                      <Skeleton className="h-4 w-32" />
                    ) : (
                      (tenant.data?.name ?? user.tenant_name ?? '—')
                    )}
                  </Row>
                  <Row label={t.mfa}>{user.mfa_enabled ? t.enabled : t.disabled}</Row>
                </dl>
              </CardContent>
            </Card>
          </StaggerItem>

          <StaggerItem>
            <p className="text-sm text-muted-foreground">{t.scopeNote}</p>
          </StaggerItem>
        </Stagger>
      </main>
    </div>
  );
}
