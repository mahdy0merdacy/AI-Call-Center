import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { messages } from '@/i18n';
import { userMessage } from '@/lib/errors';
import { cn } from '@/lib/utils';

const t = messages.insights;

export function SectionCard({
  title,
  description,
  action,
  className,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Card className={className}>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0">
        <div className="space-y-1.5">
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">{children}</p>
  );
}

export function LinesSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label={messages.common.loading}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn('h-4', i % 2 === 0 ? 'w-3/4' : 'w-1/2')} />
      ))}
    </div>
  );
}

/**
 * Loading, error and empty states of one section, so a failing endpoint shows
 * an inline error in its own card instead of blanking the page.
 */
export function QueryState<T>({
  query,
  skeleton,
  isEmpty,
  emptyText = t.empty,
  children,
}: {
  query: UseQueryResult<T>;
  skeleton?: ReactNode;
  isEmpty?: (data: T) => boolean;
  emptyText?: string;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <>{skeleton ?? <LinesSkeleton />}</>;
  if (query.isError) {
    return (
      <Alert
        variant="destructive"
        title={t.loadFailed}
        aside={
          <Button variant="outline" size="sm" onClick={() => void query.refetch()} loading={query.isFetching}>
            {t.retry}
          </Button>
        }
      >
        {userMessage(query.error)}
      </Alert>
    );
  }
  if (isEmpty?.(query.data)) return <EmptyState>{emptyText}</EmptyState>;
  return <>{children(query.data)}</>;
}

/** A labelled horizontal bar (share 0..1 of the widest bar), for small rankings. */
export function MeterRow({
  label,
  value,
  fraction,
  barClassName = 'bg-primary',
}: {
  label: ReactNode;
  value: ReactNode;
  fraction: number;
  barClassName?: string;
}) {
  const width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
  return (
    <li className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 truncate">{label}</span>
        <span className="shrink-0 tabular-nums text-muted-foreground">{value}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
        <div className={cn('h-full rounded-full', barClassName)} style={{ width }} />
      </div>
    </li>
  );
}
