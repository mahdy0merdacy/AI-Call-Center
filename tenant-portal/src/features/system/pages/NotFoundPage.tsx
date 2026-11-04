import { Compass } from 'lucide-react';
import { Link } from 'react-router-dom';
import { paths } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { Button } from '@/components/ui/button';
import { messages } from '@/i18n';

export function NotFoundPage() {
  return (
    <AuthLayout
      icon={
        <span className="flex size-12 items-center justify-center rounded-xl bg-accent text-accent-foreground">
          <Compass className="size-6" aria-hidden />
        </span>
      }
      title={messages.notFound.title}
      description={messages.notFound.description}
    >
      <Button asChild block>
        <Link to={paths.home}>{messages.notFound.cta}</Link>
      </Button>
    </AuthLayout>
  );
}
