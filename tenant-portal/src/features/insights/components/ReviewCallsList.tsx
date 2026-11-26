import { ChevronDown } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';
import { emotionBadgeVariant, emotionLabel } from '../emotions';
import { formatDateTime, formatDurationSeconds } from '../format';
import type { ReviewCall } from '../types';

const t = messages.insights.review;

function reasonLabel(reason: string): string {
  return reason === 'escalated' || reason === 'negative_end' ? t.reasons[reason] : reason;
}

function ReviewCallItem({ call }: { call: ReviewCall }) {
  const reasons = call.reason
    .split(',')
    .map((r) => r.trim())
    .filter(Boolean);

  return (
    <li>
      <details className="group">
        <summary className="flex cursor-pointer list-none flex-col gap-2 px-4 py-3 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/25 sm:flex-row sm:items-center sm:justify-between [&::-webkit-details-marker]:hidden">
          <div className="flex flex-wrap items-center gap-2">
            {reasons.map((reason) => (
              <Badge key={reason} variant={reason === 'escalated' ? 'warning' : 'destructive'}>
                {reasonLabel(reason)}
              </Badge>
            ))}
            <span className="text-sm font-medium">{formatDateTime(call.started_at)}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {call.last_emotion && (
              <span className="flex items-center gap-1.5">
                {t.lastEmotion}
                <Badge variant={emotionBadgeVariant(call.last_emotion)}>{emotionLabel(call.last_emotion)}</Badge>
              </span>
            )}
            <span>
              {t.duration} <span className="tabular-nums text-foreground">{formatDurationSeconds(call.duration_s)}</span>
            </span>
            <span className="tabular-nums">{t.turns(call.turns)}</span>
            <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
          </div>
        </summary>
        <div className="border-t bg-muted/30 px-4 py-3">
          <h4 className="sr-only">{t.transcript}</h4>
          {call.transcript.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t.noTranscript}</p>
          ) : (
            <ol className="max-h-80 space-y-2 overflow-y-auto pr-1 text-sm">
              {call.transcript.map((line, i) => (
                <li key={i} className={cn('flex gap-3', line.who === 'agent' && 'text-muted-foreground')}>
                  <span
                    className={cn(
                      'w-16 shrink-0 text-xs font-semibold uppercase tracking-wide',
                      line.who === 'agent' ? 'text-primary' : 'text-foreground',
                    )}
                  >
                    {line.who === 'agent' ? t.agent : t.caller}
                  </span>
                  <span className="min-w-0">{line.text}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </details>
    </li>
  );
}

export function ReviewCallsList({ calls }: { calls: ReviewCall[] }) {
  return (
    <ul className="divide-y overflow-hidden rounded-lg border">
      {calls.map((call) => (
        <ReviewCallItem key={call.call_id} call={call} />
      ))}
    </ul>
  );
}
