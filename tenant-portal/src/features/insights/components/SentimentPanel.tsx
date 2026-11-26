import { messages } from '@/i18n';
import { emotionBarClass, emotionLabel } from '../emotions';
import { formatInt, formatRate } from '../format';
import type { InsightsSentiment } from '../types';
import { EmptyState, MeterRow } from './SectionCard';

const t = messages.insights.sentiment;

export function SentimentPanel({ sentiment }: { sentiment: InsightsSentiment }) {
  const emotions = [...sentiment.emotions].sort((a, b) => b.share - a.share);
  const maxShare = Math.max(...emotions.map((e) => e.share), 0);
  const { calls } = sentiment;
  const share = (n: number) => (calls.total > 0 ? formatRate(n / calls.total) : '—');

  const stats = [
    { key: 'risk', label: t.escalationRisk, value: calls.escalation_risk },
    { key: 'improving', label: t.improving, value: calls.improving },
    { key: 'stable', label: t.stable, value: calls.stable },
    { key: 'worsening', label: t.worsening, value: calls.worsening },
    { key: 'negative', label: t.endedNegative, value: calls.ended_negative },
  ];

  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-semibold">{t.mixTitle}</h3>
          <p className="text-sm text-muted-foreground">{t.mixDescription}</p>
        </div>
        {emotions.length === 0 ? (
          <EmptyState>{t.empty}</EmptyState>
        ) : (
          <ul className="space-y-3">
            {emotions.map((e) => (
              <MeterRow
                key={e.emotion}
                label={emotionLabel(e.emotion)}
                value={
                  <span title={t.utterances(e.utterances)}>
                    {formatRate(e.share)}
                    <span className="sr-only"> ({t.utterances(e.utterances)})</span>
                  </span>
                }
                fraction={maxShare > 0 ? e.share / maxShare : 0}
                barClassName={emotionBarClass(e.emotion)}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold">{t.callsTitle}</h3>
        <dl className="divide-y rounded-lg border">
          <div className="flex items-baseline justify-between gap-3 px-4 py-3">
            <dt className="text-sm text-muted-foreground">{t.total}</dt>
            <dd className="text-lg font-semibold tabular-nums">{formatInt(calls.total)}</dd>
          </div>
          {stats.map((stat) => (
            <div key={stat.key} className="flex items-baseline justify-between gap-3 px-4 py-2.5">
              <dt className="text-sm">{stat.label}</dt>
              <dd className="text-sm tabular-nums">
                <span className="font-semibold">{formatInt(stat.value)}</span>
                <span className="ml-2 text-muted-foreground">{share(stat.value)}</span>
              </dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
