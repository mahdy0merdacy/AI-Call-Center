import { messages } from '@/i18n';
import { cn } from '@/lib/utils';
import { formatInt, formatMs, formatRate } from '../format';
import type { InsightsTools } from '../types';

const t = messages.insights.tools;

const th = 'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground';
const num = 'text-right tabular-nums';

function rateClass(rate: number | null): string {
  if (rate === null) return 'text-muted-foreground';
  if (rate < 0.8) return 'text-destructive';
  if (rate < 0.95) return 'text-warning';
  return '';
}

export function ToolsTable({ tools }: { tools: InsightsTools['tools'] }) {
  const rows = [...tools].sort((a, b) => b.calls - a.calls);
  return (
    <div className="-mx-2 overflow-x-auto">
      <table className="w-full min-w-[32rem] text-sm">
        <caption className="sr-only">{t.title}</caption>
        <thead className="border-b">
          <tr>
            <th scope="col" className={th}>
              {t.tool}
            </th>
            <th scope="col" className={cn(th, 'text-right')}>
              {t.calls}
            </th>
            <th scope="col" className={cn(th, 'text-right')}>
              {t.successRate}
            </th>
            <th scope="col" className={cn(th, 'text-right')}>
              {t.errors}
            </th>
            <th scope="col" className={cn(th, 'text-right')}>
              {t.timeouts}
            </th>
            <th scope="col" className={cn(th, 'text-right')}>
              {t.avgLatency}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((tool) => (
            <tr key={tool.tool}>
              <td className="px-3 py-2.5 font-mono text-[13px]">{tool.tool}</td>
              <td className={cn('px-3 py-2.5', num)}>{formatInt(tool.calls)}</td>
              <td className={cn('px-3 py-2.5 font-semibold', num, rateClass(tool.success_rate))}>
                {formatRate(tool.success_rate)}
              </td>
              <td className={cn('px-3 py-2.5', num)}>{formatInt(tool.errors)}</td>
              <td className={cn('px-3 py-2.5', num)}>{formatInt(tool.timeouts)}</td>
              <td className={cn('px-3 py-2.5', num)}>{formatMs(tool.avg_latency_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
