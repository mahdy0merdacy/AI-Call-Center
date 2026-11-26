import { messages } from '@/i18n';
import { WEEKDAYS } from '../format';
import type { InsightsActivity } from '../types';

const t = messages.insights.activity;
const HOURS = Array.from({ length: 24 }, (_, h) => h);

function cellColor(calls: number, max: number): string | undefined {
  if (calls <= 0 || max <= 0) return undefined;
  // Never fully transparent, so a single call still shows.
  return `hsl(var(--primary) / ${(0.12 + 0.88 * (calls / max)).toFixed(2)})`;
}

/** Weekday (rows, Monday first) x hour (columns, UTC) grid; darker = more calls. */
export function ActivityHeatmap({ heatmap }: { heatmap: InsightsActivity['heatmap'] }) {
  const grid = WEEKDAYS.map(() => HOURS.map(() => 0));
  for (const { weekday, hour, calls } of heatmap) {
    const row = grid[weekday];
    if (row && hour >= 0 && hour < 24) row[hour] = (row[hour] ?? 0) + calls;
  }
  const max = Math.max(0, ...grid.flat());

  return (
    <div className="space-y-3">
      <div className="-mx-2 overflow-x-auto px-2">
        <div
          className="grid min-w-[36rem] gap-[3px] text-[11px] text-muted-foreground"
          style={{ gridTemplateColumns: 'auto repeat(24, minmax(0, 1fr))' }}
        >
          <span />
          {HOURS.map((h) => (
            <span key={h} className="text-center tabular-nums" aria-hidden>
              {h % 3 === 0 ? h : ''}
            </span>
          ))}
          {WEEKDAYS.map((day, weekday) => (
            <div key={day} className="contents">
              <span className="pr-2 leading-5">{day}</span>
              {HOURS.map((hour) => {
                const calls = grid[weekday]?.[hour] ?? 0;
                const label = t.cell(day, hour, calls);
                return (
                  <span
                    key={hour}
                    role="img"
                    aria-label={label}
                    title={label}
                    className="h-5 rounded-[3px] bg-muted"
                    style={{ backgroundColor: cellColor(calls, max) }}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>{t.utcNote}</span>
        <span className="flex items-center gap-1.5" aria-hidden>
          {t.fewer}
          {[0.12, 0.34, 0.56, 0.78, 1].map((alpha) => (
            <span key={alpha} className="size-3 rounded-[3px]" style={{ backgroundColor: `hsl(var(--primary) / ${alpha})` }} />
          ))}
          {t.more}
        </span>
      </div>
    </div>
  );
}
