import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { getActivity, getKnowledge, getReviewCalls, getSentiment, getSummary, getTools } from '../api';
import { insightsKeys, type InsightsSection } from '../queryKeys';
import type { InsightsFilters } from '../types';

/**
 * One query per section, so a slow or failing endpoint only affects its own
 * card. Changing the filters keeps the previous figures on screen until the
 * new ones arrive (no skeleton flash).
 */
function useInsightsQuery<T>(section: InsightsSection, filters: InsightsFilters, fetcher: (f: InsightsFilters) => Promise<T>) {
  return useQuery({
    queryKey: insightsKeys.section(section, filters),
    queryFn: () => fetcher(filters),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
}

export const useInsightsSummary = (filters: InsightsFilters) => useInsightsQuery('summary', filters, getSummary);
export const useInsightsSentiment = (filters: InsightsFilters) => useInsightsQuery('sentiment', filters, getSentiment);
export const useInsightsTools = (filters: InsightsFilters) => useInsightsQuery('tools', filters, getTools);
export const useInsightsKnowledge = (filters: InsightsFilters) => useInsightsQuery('knowledge', filters, getKnowledge);
export const useInsightsActivity = (filters: InsightsFilters) => useInsightsQuery('activity', filters, getActivity);
export const useInsightsReviewCalls = (filters: InsightsFilters) => useInsightsQuery('calls', filters, getReviewCalls);
