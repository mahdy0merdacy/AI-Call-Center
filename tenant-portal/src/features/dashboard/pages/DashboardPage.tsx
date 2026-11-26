/**
 * Signed-in home: a sample call replaying the tenant's own process on the call
 * console, the button to call the real agent, and the account summary.
 */
import { ArrowRight, BarChart3, Building2, FileText, LogOut, Settings } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useAuthedUser } from '@/app/guards';
import { paths } from '@/app/paths';
import { AppHeader } from '@/components/layout/AppHeader';
import { Stagger, StaggerItem } from '@/components/motion';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { CallConsole, LiveDot } from '@/components/voice/CallConsole';
import { callsForTenant } from '@/components/voice/demoCalls';
import { useCurrentTenant } from '@/features/auth/hooks/useCurrentUser';
import { useSession } from '@/features/auth/session/SessionProvider';
import { messages } from '@/i18n';
import { useDocumentTitle } from '@/lib/hooks/useDocumentTitle';
import { TestAgentCard } from '../components/TestAgentCard';

const t = messages.dashboard;

const dashboardLinkClass =
  'group inline-flex items-center gap-2 text-sm font-semibold text-foreground underline decoration-border decoration-2 underline-offset-[6px] transition-colors hover:decoration-primary focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/25';

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
  const hasTenant = user.tenant_id !== null;
  const tenant = useCurrentTenant(hasTenant);
  const { signOut } = useSession();
  const [signingOut, setSigningOut] = useState(false);

  const handleSignOut = async () => {
    setSigningOut(true);
    await signOut();
  };

  const displayName = user.full_name || user.email;
  const tenantSlug = tenant.data?.slug ?? user.tenant_slug;
  const tenantName = tenant.data?.name ?? user.tenant_name;
  const calls = useMemo(() => callsForTenant(tenantSlug), [tenantSlug]);
  useDocumentTitle(messages.nav.dashboard);

  return (
    <div className="relative isolate min-h-dvh bg-background">
      <div
        aria-hidden
        className="paper-grid pointer-events-none absolute inset-x-0 top-0 -z-10 h-[640px] [mask-image:linear-gradient(to_bottom,black_20%,transparent)]"
      />
      <AppHeader />

      <main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-8 focus:outline-none sm:px-6 sm:py-14">
        <Stagger className="space-y-12">
          <StaggerItem>
            <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.12fr)] lg:gap-14">
              <div className="space-y-8">
                <div className="space-y-5">
                  <div className="flex items-center gap-2.5 font-mono text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
                    <LiveDot />
                    {hasTenant ? (
                      tenant.isPending && !tenantName ? (
                        <Skeleton className="h-3.5 w-28" />
                      ) : (
                        (tenantName ?? '—')
                      )
                    ) : (
                      messages.roles[user.role]
                    )}
                  </div>
                  <h1 className="font-serif text-[2.9rem] font-normal leading-[0.95] tracking-[-0.015em] [overflow-wrap:anywhere] sm:text-[4.2rem]">
                    {t.welcome}
                    <br />
                    <em className="text-primary">{displayName}</em>
                  </h1>
                  <p className="max-w-md text-[15.5px] leading-relaxed text-muted-foreground">
                    {hasTenant ? t.description : t.descriptionPlatform}
                  </p>
                </div>

                {hasTenant ? (
                  <TestAgentCard slug={tenantSlug} loading={tenantSlug === null && tenant.isPending} />
                ) : (
                  user.role === 'platform_admin' && (
                    <Button asChild size="lg" variant="outline" className="rounded-full">
                      <Link to={paths.adminTenants}>
                        <Building2 className="size-4" aria-hidden />
                        {t.manageTenants}
                        <ArrowRight className="size-4" aria-hidden />
                      </Link>
                    </Button>
                  )
                )}

                {user.role === 'tenant_admin' && hasTenant && (
                  <div className="flex flex-wrap gap-x-6 gap-y-3">
                    <Link to={paths.documents} className={dashboardLinkClass}>
                      <FileText className="size-4 text-primary" aria-hidden />
                      {t.addDocuments}
                      <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
                    </Link>
                    <Link to={paths.insights} className={dashboardLinkClass}>
                      <BarChart3 className="size-4 text-primary" aria-hidden />
                      {t.viewInsights}
                      <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
                    </Link>
                  </div>
                )}
              </div>

              <CallConsole
                calls={calls}
                controls
                className="shadow-[0_40px_80px_-40px_hsl(var(--shadow-color)/0.55)]"
                transcriptClassName="h-[300px] sm:h-[330px]"
              />
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
                    {!hasTenant ? (
                      t.noOrganization
                    ) : tenant.isPending ? (
                      <Skeleton className="h-4 w-32" />
                    ) : (
                      (tenantName ?? '—')
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
