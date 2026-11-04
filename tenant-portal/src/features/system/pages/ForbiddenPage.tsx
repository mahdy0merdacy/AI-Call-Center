import { ShieldX } from 'lucide-react';
import { Link } from 'react-router-dom';
import { paths } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem } from '@/components/motion';
import { Button } from '@/components/ui/button';
import { messages } from '@/i18n';

export function ForbiddenPage() {
  return (
    <AuthLayout
      icon={
        <span className="flex size-12 items-center justify-center rounded-xl bg-warning/15 text-warning">
          <ShieldX className="size-6" aria-hidden />
        </span>
      }
      title={messages.forbidden.title}
      description={messages.forbidden.body}
    >
      <StaggerItem>
        <Button asChild block size="lg">
          <Link to={paths.dashboard}>{messages.forbidden.cta}</Link>
        </Button>
      </StaggerItem>
    </AuthLayout>
  );
}
