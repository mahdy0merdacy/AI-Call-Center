import { ArrowRight, CircleHelp } from 'lucide-react';
import { Link } from 'react-router-dom';
import { paths } from '@/app/paths';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { messages } from '@/i18n';
import { formatDay, formatInt, formatRate } from '../format';
import type { InsightsKnowledge } from '../types';
import { EmptyState, MeterRow } from './SectionCard';

const t = messages.insights.knowledge;

/** The unanswered questions come first: they are what a tenant can act on today. */
export function KnowledgePanel({ knowledge }: { knowledge: InsightsKnowledge }) {
  const gaps = [...knowledge.gaps].sort((a, b) => b.asked - a.asked);
  const documents = [...knowledge.documents].sort((a, b) => b.uses - a.uses);
  const maxUses = Math.max(...documents.map((d) => d.uses), 0);
  const coverage = knowledge.coverage_rate;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <section className="space-y-3" aria-labelledby="knowledge-gaps">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h3 id="knowledge-gaps" className="flex items-center gap-2 text-base font-semibold">
              <CircleHelp className="size-4 text-primary" aria-hidden />
              {t.gapsTitle}
            </h3>
            <p className="max-w-prose text-sm text-muted-foreground">{t.gapsDescription}</p>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link to={paths.documents}>
              {t.addDocuments}
              <ArrowRight className="size-4" aria-hidden />
            </Link>
          </Button>
        </div>
        {gaps.length === 0 ? (
          <EmptyState>{t.noGaps}</EmptyState>
        ) : (
          <ol className="divide-y rounded-lg border">
            {gaps.map((gap) => (
              <li key={gap.question} className="flex flex-col gap-1.5 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="font-medium">“{gap.question}”</p>
                <p className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant={gap.asked >= 5 ? 'warning' : 'neutral'}>{t.asked(gap.asked)}</Badge>
                  {gap.last_day && <span>{t.lastAsked(formatDay(gap.last_day))}</span>}
                </p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <div className="space-y-8">
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">{t.coverage}</h3>
          <p className="text-3xl font-semibold tabular-nums">{formatRate(coverage)}</p>
          <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
            <div className="h-full rounded-full bg-success" style={{ width: `${(coverage ?? 0) * 100}%` }} />
          </div>
          <p className="text-sm text-muted-foreground">
            {t.coverageDetail(knowledge.covered, knowledge.searches)}
            {knowledge.failed > 0 && <> · {t.failed(knowledge.failed)}</>}
          </p>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">{t.documentsTitle}</h3>
          {documents.length === 0 ? (
            <EmptyState>{t.noDocuments}</EmptyState>
          ) : (
            <ul className="space-y-3">
              {documents.map((doc) => (
                <MeterRow
                  key={doc.title}
                  label={<span title={doc.title}>{doc.title}</span>}
                  value={<span title={t.uses(doc.uses)}>{formatInt(doc.uses)}</span>}
                  fraction={maxUses > 0 ? doc.uses / maxUses : 0}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
