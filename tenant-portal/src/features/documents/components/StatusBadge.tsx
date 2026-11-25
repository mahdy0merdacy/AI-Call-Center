import { CheckCircle2, Clock, Loader2, XCircle, type LucideIcon } from 'lucide-react';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { messages } from '@/i18n';
import type { IngestionStatus } from '../types';

const t = messages.documents.status;

const STATUS: Record<IngestionStatus, { variant: NonNullable<BadgeProps['variant']>; Icon: LucideIcon; spin?: boolean }> = {
  pending: { variant: 'neutral', Icon: Clock },
  processing: { variant: 'info', Icon: Loader2, spin: true },
  completed: { variant: 'success', Icon: CheckCircle2 },
  failed: { variant: 'destructive', Icon: XCircle },
};

export function StatusBadge({ status }: { status: IngestionStatus }) {
  const { variant, Icon, spin } = STATUS[status];
  return (
    <Badge variant={variant}>
      <Icon className={spin ? 'animate-spin motion-reduce:animate-none' : undefined} aria-hidden />
      {t[status]}
    </Badge>
  );
}
