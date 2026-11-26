/**
 * Call analytics for a tenant, served by the insights service: KPIs, the
 * knowledge gaps (questions the documents don't answer), daily volume,
 * caller sentiment, tools, activity and the calls worth a listen.
 *
 * Each section has its own query, so one failing endpoint shows an inline
 * error in its card while the rest of the page keeps working. The filters
 * live in the URL (`?days=30&synthetic=exclude`) so a view can be shared.
 */
import { useId } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AppHeader } from '@/components/layout/AppHeader';
import { Stagger, StaggerItem } from '@/components/motion';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { messages } from '@/i18n';
import { useDocumentTitle } from '@/lib/hooks/useDocumentTitle';
import { ActivityHeatmap } from '../components/ActivityHeatmap';
import { DailyCallsChart } from '../components/DailyCallsChart';
import { KnowledgePanel } from '../components/KnowledgePanel';
import { KpiTiles, KpiTilesSkeleton } from '../components/KpiTiles';
import { ReviewCallsList } from '../components/ReviewCallsList';
import { LinesSkeleton, QueryState, SectionCard } from '../components/SectionCard';
import { SentimentPanel } from '../components/SentimentPanel';
import { ToolsTable } from '../components/ToolsTable';
import { formatDateTime } from '../format';
import {
  useInsightsActivity,
  useInsightsKnowledge,
  useInsightsReviewCalls,
  useInsightsSentiment,
  useInsightsSummary,
  useInsightsTools,
} from '../hooks/useInsights';
import { RANGE_DAYS, type InsightsFilters, type RangeDays } from '../types';

const t = messages.insights;
const DEFAULT_DAYS: RangeDays = 30;

function parseDays(value: string | null): RangeDays {
  const days = Number(value);
  return (RANGE_DAYS as readonly number[]).includes(days) ? (days as RangeDays) : DEFAULT_DAYS;
}

function useFilters(): [InsightsFilters, (next: Partial<InsightsFilters>) => void] {
  const [params, setParams] = useSearchParams();
  const filters: InsightsFilters = {
    days: parseDays(params.get('days')),
    // Simulated calls are shown unless explicitly turned off.
    synthetic: params.get('synthetic') === 'exclude' ? 'exclude' : 'include',
  };
  const update = (next: Partial<InsightsFilters>) => {
    const merged = { ...filters, ...next };
    const search = new URLSearchParams();
    if (merged.days !== DEFAULT_DAYS) search.set('days', String(merged.days));
    if (merged.synthetic === 'exclude') search.set('synthetic', 'exclude');
    setParams(search, { replace: true });
  };
  return [filters, update];
}

function Filters({ filters, onChange }: { filters: InsightsFilters; onChange: (next: Partial<InsightsFilters>) => void }) {
  const syntheticId = useId();
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3" role="group" aria-label={t.filters}>
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground" id={`${syntheticId}-range`}>
          {t.rangeLabel}
        </span>
        <div className="inline-flex gap-1 rounded-xl bg-muted p-1" role="group" aria-labelledby={`${syntheticId}-range`}>
          {RANGE_DAYS.map((days) => (
            <Button
              key={days}
              size="sm"
              variant={filters.days === days ? 'outline' : 'ghost'}
              aria-pressed={filters.days === days}
              onClick={() => onChange({ days })}
            >
              {t.rangeDays(days)}
            </Button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2.5">
        <Checkbox
          id={syntheticId}
          checked={filters.synthetic === 'include'}
          onCheckedChange={(checked) => onChange({ synthetic: checked === true ? 'include' : 'exclude' })}
          aria-describedby={`${syntheticId}-hint`}
        />
        <div className="leading-tight">
          <Label htmlFor={syntheticId} className="cursor-pointer">
            {t.syntheticLabel}
          </Label>
          <p id={`${syntheticId}-hint`} className="text-xs text-muted-foreground">
            {t.syntheticHint}
          </p>
        </div>
      </div>
    </div>
  );
}

export function InsightsPage() {
  useDocumentTitle(t.title);
  const [filters, setFilters] = useFilters();

  const summary = useInsightsSummary(filters);
  const sentiment = useInsightsSentiment(filters);
  const tools = useInsightsTools(filters);
  const knowledge = useInsightsKnowledge(filters);
  const activity = useInsightsActivity(filters);
  const reviewCalls = useInsightsReviewCalls(filters);

  const refreshing = [summary, sentiment, tools, knowledge, activity, reviewCalls].some(
    (q) => q.isPlaceholderData && q.isFetching,
  );

  return (
    <div className="min-h-dvh bg-background">
      <AppHeader />
      <main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-10 focus:outline-none sm:px-6">
        <Stagger className="space-y-6">
          <StaggerItem>
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <h1 className="font-serif text-[2.6rem] font-normal leading-none tracking-[-0.01em]">{t.title}</h1>
                <p className="mt-1 max-w-2xl text-muted-foreground">{t.description}</p>
              </div>
              <div className="text-sm text-muted-foreground" aria-live="polite">
                {refreshing ? (
                  t.refreshing
                ) : summary.isPending ? (
                  <Skeleton className="h-4 w-40" />
                ) : summary.data?.updated_at ? (
                  t.updatedAt(formatDateTime(summary.data.updated_at))
                ) : summary.isSuccess ? (
                  t.neverUpdated
                ) : null}
              </div>
            </div>
          </StaggerItem>

          <StaggerItem>
            <Filters filters={filters} onChange={setFilters} />
          </StaggerItem>

          <StaggerItem>
            <section aria-label={t.kpis.title}>
              <QueryState
                query={summary}
                skeleton={<KpiTilesSkeleton />}
                isEmpty={(data) => data.kpis.calls === 0}
              >
                {(data) => <KpiTiles kpis={data.kpis} previous={data.previous} />}
              </QueryState>
            </section>
          </StaggerItem>

          <StaggerItem>
            <SectionCard title={t.knowledge.title} description={t.knowledge.description} className="border-primary/30">
              <QueryState query={knowledge} isEmpty={(data) => data.searches === 0} emptyText={t.knowledge.empty}>
                {(data) => <KnowledgePanel knowledge={data} />}
              </QueryState>
            </SectionCard>
          </StaggerItem>

          <StaggerItem>
            <SectionCard title={t.daily.title} description={t.daily.description}>
              <QueryState
                query={summary}
                skeleton={<Skeleton className="h-72 w-full" />}
                isEmpty={(data) => data.kpis.calls === 0 || data.daily.length === 0}
              >
                {(data) => <DailyCallsChart daily={data.daily} days={data.range.days} />}
              </QueryState>
            </SectionCard>
          </StaggerItem>

          <StaggerItem>
            <SectionCard title={t.sentiment.title}>
              <QueryState
                query={sentiment}
                skeleton={<LinesSkeleton lines={6} />}
                isEmpty={(data) => data.calls.total === 0 && data.emotions.length === 0}
                emptyText={t.sentiment.empty}
              >
                {(data) => <SentimentPanel sentiment={data} />}
              </QueryState>
            </SectionCard>
          </StaggerItem>

          <StaggerItem>
            <SectionCard title={t.tools.title} description={t.tools.description}>
              <QueryState query={tools} isEmpty={(data) => data.tools.length === 0} emptyText={t.tools.empty}>
                {(data) => <ToolsTable tools={data.tools} />}
              </QueryState>
            </SectionCard>
          </StaggerItem>

          <StaggerItem>
            <SectionCard title={t.activity.title} description={t.activity.description}>
              <QueryState
                query={activity}
                skeleton={<Skeleton className="h-48 w-full" />}
                isEmpty={(data) => data.heatmap.every((cell) => cell.calls === 0)}
              >
                {(data) => <ActivityHeatmap heatmap={data.heatmap} />}
              </QueryState>
            </SectionCard>
          </StaggerItem>

          <StaggerItem>
            <SectionCard title={t.review.title} description={t.review.description}>
              <QueryState
                query={reviewCalls}
                skeleton={<LinesSkeleton lines={4} />}
                isEmpty={(data) => data.calls.length === 0}
                emptyText={t.review.empty}
              >
                {(data) => <ReviewCallsList calls={data.calls} />}
              </QueryState>
            </SectionCard>
          </StaggerItem>

        </Stagger>
      </main>
    </div>
  );
}
