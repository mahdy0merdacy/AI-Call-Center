import { CloudOff, RefreshCw } from 'lucide-react';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem } from '@/components/motion';
import { Button } from '@/components/ui/button';
import { messages } from '@/i18n';
import { userMessage } from '@/lib/errors';

interface FullPageErrorProps {
  title: string;
  error: unknown;
  onRetry: () => void;
  retrying?: boolean;
}

export function FullPageError({ title, error, onRetry, retrying = false }: FullPageErrorProps) {
  return (
    <AuthLayout
      icon={
        <span className="flex size-12 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
          <CloudOff className="size-6" aria-hidden />
        </span>
      }
      title={title}
      description={userMessage(error)}
    >
      <StaggerItem>
        <Button block size="lg" onClick={onRetry} loading={retrying} loadingText={messages.common.loading}>
          <RefreshCw className="size-4" aria-hidden />
          {messages.common.retry}
        </Button>
      </StaggerItem>
    </AuthLayout>
  );
}
