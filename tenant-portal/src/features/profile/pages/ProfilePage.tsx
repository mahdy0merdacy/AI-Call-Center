import { Shield, UserRound } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { AppHeader } from '@/components/layout/AppHeader';
import { Stagger, StaggerItem } from '@/components/motion';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { messages } from '@/i18n';
import { useDocumentTitle } from '@/lib/hooks/useDocumentTitle';
import { ProfileTab } from '../components/ProfileTab';
import { SecurityTab } from '../components/SecurityTab';

const t = messages.profile;
type Tab = 'profile' | 'security';

export function ProfilePage() {
  // ?tab=security deep-links to the security tab.
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get('tab') === 'security' ? 'security' : 'profile';
  useDocumentTitle(`${tab === 'security' ? t.tabSecurity : t.tabProfile} · ${t.title}`);

  return (
    <div className="min-h-dvh bg-background">
      <AppHeader />
      <main id="main" tabIndex={-1} className="mx-auto max-w-3xl px-4 py-10 focus:outline-none sm:px-6">
        <Stagger className="space-y-6">
          <StaggerItem>
            <h1 className="text-3xl font-bold">{t.title}</h1>
            <p className="mt-1 text-muted-foreground">{t.description}</p>
          </StaggerItem>
          <StaggerItem>
            <Tabs
              value={tab}
              onValueChange={(value) =>
                setParams(value === 'security' ? { tab: 'security' } : {}, { replace: true })
              }
            >
              <TabsList>
                <TabsTrigger value="profile">
                  <UserRound aria-hidden />
                  {t.tabProfile}
                </TabsTrigger>
                <TabsTrigger value="security">
                  <Shield aria-hidden />
                  {t.tabSecurity}
                </TabsTrigger>
              </TabsList>
              <TabsContent value="profile">
                <ProfileTab />
              </TabsContent>
              <TabsContent value="security">
                <SecurityTab />
              </TabsContent>
            </Tabs>
          </StaggerItem>
        </Stagger>
      </main>
    </div>
  );
}
