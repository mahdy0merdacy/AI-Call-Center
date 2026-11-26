import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';
import {
  formatDurationSeconds,
  formatInt,
  formatMs,
  formatRate,
  rateChange,
  relativeChange,
  type Change,
} from '../format';
import type { Kpis } from '../types';

const t = messages.insights.kpis;

interface Tile {
  key: string;
  label: string;
  value: string;
  change: Change | null;
  hint?: string;
}

function tiles(kpis: Kpis, previous: Kpis | null): Tile[] {
  const p = previous;
  return [
    {
      key: 'calls',
      label: t.calls,
      value: formatInt(kpis.calls),
      change: p ? relativeChange(kpis.calls, p.calls, 'none') : null,
    },
    {
      key: 'self-service',
      label: t.selfService,
      value: formatRate(kpis.self_service_rate),
      change: p ? rateChange(kpis.self_service_rate, p.self_service_rate, 'up') : null,
    },
    {
      key: 'escalation',
      label: t.escalation,
      value: formatRate(kpis.escalation_rate),
      change: p ? rateChange(kpis.escalation_rate, p.escalation_rate, 'down') : null,
      hint: t.escalatedCalls(kpis.escalated_calls),
    },
    {
      key: 'duration',
      label: t.avgDuration,
      value: formatDurationSeconds(kpis.avg_duration_s),
      change: p ? relativeChange(kpis.avg_duration_s, p.avg_duration_s, 'none') : null,
    },
    {
      key: 'tools',
      label: t.toolSuccess,
      value: formatRate(kpis.tool_success_rate),
      change: p ? rateChange(kpis.tool_success_rate, p.tool_success_rate, 'up') : null,
    },
    {
      key: 'latency',
      label: t.latency,
      value: formatMs(kpis.turn_p50_ms),
      change: p ? relativeChange(kpis.turn_p50_ms, p.turn_p50_ms, 'down') : null,
    },
  ];
}

const TONE_CLASS = {
  good: 'text-success',
  bad: 'text-destructive',
  neutral: 'text-muted-foreground',
} as const;

const DIRECTION_ICON = { up: ArrowUpRight, down: ArrowDownRight, flat: Minus } as const;

function ChangeLine({ change }: { change: Change | null }) {
  if (!change) return <p className="text-xs text-muted-foreground">{t.noPrevious}</p>;
  const Icon = DIRECTION_ICON[change.direction];
  return (
    <p className="flex flex-wrap items-center gap-x-1 text-xs">
      <span className={cn('inline-flex items-center gap-0.5 font-semibold tabular-nums', TONE_CLASS[change.tone])}>
        <Icon className="size-3.5" aria-hidden />
        {change.text}
      </span>
      <span className="text-muted-foreground">{t.vsPrevious}</span>
    </p>
  );
}

export function KpiTiles({ kpis, previous }: { kpis: Kpis; previous: Kpis | null }) {
  return (
    <ul className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
      {tiles(kpis, previous).map((tile) => (
        <li key={tile.key}>
          <Card className="flex h-full flex-col gap-2 p-4">
            <p className="text-sm text-muted-foreground">{tile.label}</p>
            <p className="text-2xl font-semibold tabular-nums">{tile.value}</p>
            {tile.hint && <p className="text-xs text-muted-foreground">{tile.hint}</p>}
            <div className="mt-auto">
              <ChangeLine change={tile.change} />
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}

export function KpiTilesSkeleton() {
  return (
    <ul className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6" aria-busy="true" aria-label={messages.common.loading}>
      {Array.from({ length: 6 }, (_, i) => (
        <li key={i}>
          <Card className="space-y-3 p-4">
            <Skeleton className="h-3.5 w-24" />
            <Skeleton className="h-7 w-16" />
            <Skeleton className="h-3 w-28" />
          </Card>
        </li>
      ))}
    </ul>
  );
}
