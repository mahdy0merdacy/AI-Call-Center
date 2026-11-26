/**
 * Calls to the insights service (call analytics). The web gateway routes
 * /api/insights/ to it; the tenant always comes from the access token.
 *
 * Every endpoint is a GET taking `days=7|30|90` and `synthetic=include|exclude|only`:
 *
 *   /api/insights/summary     -> KPIs, previous period, per-day calls
 *   /api/insights/sentiment   -> emotion mix, per-call mood trends
 *   /api/insights/tools       -> per-tool usage and reliability
 *   /api/insights/knowledge   -> document coverage and unanswered questions
 *   /api/insights/activity    -> weekday x hour (UTC) heatmap
 *   /api/insights/calls       -> calls to review, with transcripts (`limit`)
 */
import type { z } from 'zod';
import { insightsApi } from '@/lib/apiClient';
import {
  activitySchema,
  knowledgeSchema,
  reviewCallsSchema,
  sentimentSchema,
  summarySchema,
  toolsSchema,
  type InsightsFilters,
} from './types';

export const REVIEW_CALLS_LIMIT = 20;

async function getInsights<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  filters: InsightsFilters,
  extra: Record<string, string | number> = {},
): Promise<z.output<S>> {
  const { data } = await insightsApi.get<unknown>(`/insights/${path}`, {
    params: { days: filters.days, synthetic: filters.synthetic, ...extra },
  });
  return schema.parse(data) as z.output<S>;
}

export const getSummary = (filters: InsightsFilters) => getInsights('summary', summarySchema, filters);
export const getSentiment = (filters: InsightsFilters) => getInsights('sentiment', sentimentSchema, filters);
export const getTools = (filters: InsightsFilters) => getInsights('tools', toolsSchema, filters);
export const getKnowledge = (filters: InsightsFilters) => getInsights('knowledge', knowledgeSchema, filters);
export const getActivity = (filters: InsightsFilters) => getInsights('activity', activitySchema, filters);
export const getReviewCalls = (filters: InsightsFilters) =>
  getInsights('calls', reviewCallsSchema, filters, { limit: REVIEW_CALLS_LIMIT });
