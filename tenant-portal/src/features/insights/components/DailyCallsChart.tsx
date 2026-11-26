import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { messages } from '@/i18n';
import { formatDay, formatInt } from '../format';
import type { InsightsSummary } from '../types';

const t = messages.insights.daily;

// Both series count calls, so they share one axis.
const CALLS_COLOR = 'hsl(var(--primary) / 0.45)';
const ESCALATED_COLOR = 'hsl(var(--foreground))';
const AXIS_COLOR = 'hsl(var(--muted-foreground))';

export function DailyCallsChart({ daily, days }: { daily: InsightsSummary['daily']; days: number }) {
  return (
    <div role="img" aria-label={t.chartLabel(days)} className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={daily} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid vertical={false} stroke="hsl(var(--border))" />
          <XAxis
            dataKey="day"
            tickFormatter={(day: string) => formatDay(day)}
            tick={{ fill: AXIS_COLOR, fontSize: 12 }}
            tickLine={false}
            axisLine={{ stroke: 'hsl(var(--border))' }}
            minTickGap={24}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fill: AXIS_COLOR, fontSize: 12 }}
            tickLine={false}
            axisLine={false}
            width={48}
          />
          <Tooltip
            cursor={{ fill: 'hsl(var(--muted))' }}
            labelFormatter={(day) => formatDay(String(day), true)}
            formatter={(value) => formatInt(Number(value))}
            contentStyle={{
              background: 'hsl(var(--popover))',
              border: '1px solid hsl(var(--border))',
              borderRadius: 8,
              color: 'hsl(var(--popover-foreground))',
              fontSize: 13,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 13 }} />
          <Bar dataKey="calls" name={t.calls} fill={CALLS_COLOR} radius={[4, 4, 0, 0]} maxBarSize={28} />
          <Line
            dataKey="escalated_calls"
            name={t.escalated}
            type="monotone"
            stroke={ESCALATED_COLOR}
            strokeWidth={2}
            dot={days <= 30 ? { r: 3 } : false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
