import { Ban, LifeBuoy } from 'lucide-react';
import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { paths } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem } from '@/components/motion';
import { Button } from '@/components/ui/button';
import { logoutRequest } from '@/features/auth/api/session';
import { messages } from '@/i18n';
import { tokenStore } from '@/lib/auth/tokenStore';
import { env } from '@/lib/env';

const t = messages.suspended;

export function SuspendedPage() {
  // Drop the refresh cookie: otherwise every boot would restore the session,
  // hit the suspension again and bounce back here instead of reaching /login.
  useEffect(() => {
    tokenStore.clear();
    logoutRequest().catch(() => undefined);
  }, []);

  return (
    <AuthLayout
      icon={
        <span className="flex size-12 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
          <Ban className="size-6" aria-hidden />
        </span>
      }
      title={t.title}
      description={t.body}
    >
      <StaggerItem className="space-y-3">
        <Button asChild block size="lg">
          <Link to={paths.login} replace>
            {t.back}
          </Link>
        </Button>
        {env.supportEmail && (
          <Button asChild block size="lg" variant="outline">
            <a href={`mailto:${env.supportEmail}`}>
              <LifeBuoy className="size-4" aria-hidden />
              {t.contact}
            </a>
          </Button>
        )}
      </StaggerItem>
    </AuthLayout>
  );
}
