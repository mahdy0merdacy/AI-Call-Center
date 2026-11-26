import type { InsightsFilters } from './types';

export type InsightsSection = 'summary' | 'sentiment' | 'tools' | 'knowledge' | 'activity' | 'calls';

export const insightsKeys = {
  all: ['insights'] as const,
  section: (section: InsightsSection, filters: InsightsFilters) =>
    [...insightsKeys.all, section, filters.days, filters.synthetic] as const,
};
