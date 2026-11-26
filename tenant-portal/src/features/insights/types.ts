import { z } from 'zod';

/** Periods the insights service accepts (`?days=`). */
export const RANGE_DAYS = [7, 30, 90] as const;
export type RangeDays = (typeof RANGE_DAYS)[number];

/** `?synthetic=`: simulated (test/demo) calls kept, dropped, or alone. */
export type SyntheticMode = 'include' | 'exclude' | 'only';

export interface InsightsFilters {
  days: RangeDays;
  synthetic: SyntheticMode;
}

export const EMOTIONS = ['neutral', 'satisfied', 'confused', 'frustrated', 'angry', 'anxious', 'sad'] as const;
export type Emotion = (typeof EMOTIONS)[number];

/** Rates are 0..1, or null when there is nothing to divide by. */
const rate = z.number().nullable();

/* ------------------------------------------------------------------ */
/* GET /api/insights/summary                                           */
/* ------------------------------------------------------------------ */

export const kpisSchema = z.object({
  calls: z.number(),
  self_service_rate: rate,
  escalation_rate: rate,
  avg_duration_s: z.number().nullable(),
  tool_success_rate: rate,
  turn_p50_ms: z.number().nullable(),
  escalated_calls: z.number(),
});
export type Kpis = z.infer<typeof kpisSchema>;

export const summarySchema = z.object({
  tenants: z.array(z.string()),
  range: z.object({ from: z.string(), to: z.string(), days: z.number() }),
  kpis: kpisSchema,
  /** Same figures for the period just before `range`. */
  previous: kpisSchema.nullable(),
  daily: z.array(
    z.object({
      day: z.string(),
      calls: z.number(),
      escalated_calls: z.number(),
      self_service_rate: rate,
    }),
  ),
  updated_at: z.string().nullable(),
});
export type InsightsSummary = z.infer<typeof summarySchema>;

/* ------------------------------------------------------------------ */
/* GET /api/insights/sentiment                                         */
/* ------------------------------------------------------------------ */

export const sentimentSchema = z.object({
  emotions: z.array(
    z.object({
      // A string, not the enum: a new emotion from the model must not break the page.
      emotion: z.string(),
      utterances: z.number(),
      share: z.number(),
    }),
  ),
  /** `{ day, <emotion>: n, ... }` per day. */
  daily: z.array(z.object({ day: z.string() }).catchall(z.union([z.number(), z.string()]))),
  calls: z.object({
    total: z.number(),
    escalation_risk: z.number(),
    improving: z.number(),
    stable: z.number(),
    worsening: z.number(),
    ended_negative: z.number(),
  }),
});
export type InsightsSentiment = z.infer<typeof sentimentSchema>;

/* ------------------------------------------------------------------ */
/* GET /api/insights/tools                                             */
/* ------------------------------------------------------------------ */

export const toolsSchema = z.object({
  tools: z.array(
    z.object({
      tool: z.string(),
      calls: z.number(),
      ok: z.number(),
      errors: z.number(),
      timeouts: z.number(),
      success_rate: rate,
      avg_latency_ms: z.number().nullable(),
    }),
  ),
});
export type InsightsTools = z.infer<typeof toolsSchema>;

/* ------------------------------------------------------------------ */
/* GET /api/insights/knowledge                                         */
/* ------------------------------------------------------------------ */

export const knowledgeSchema = z.object({
  searches: z.number(),
  covered: z.number(),
  uncovered: z.number(),
  failed: z.number(),
  coverage_rate: rate,
  documents: z.array(z.object({ title: z.string(), uses: z.number() })),
  /** Questions callers asked that no document answered. */
  gaps: z.array(z.object({ question: z.string(), asked: z.number(), last_day: z.string().nullable() })),
});
export type InsightsKnowledge = z.infer<typeof knowledgeSchema>;

/* ------------------------------------------------------------------ */
/* GET /api/insights/activity                                          */
/* ------------------------------------------------------------------ */

export const activitySchema = z.object({
  /** weekday 0 = Monday, hour 0-23 in UTC. Missing cells mean no calls. */
  heatmap: z.array(z.object({ weekday: z.number(), hour: z.number(), calls: z.number() })),
});
export type InsightsActivity = z.infer<typeof activitySchema>;

/* ------------------------------------------------------------------ */
/* GET /api/insights/calls                                             */
/* ------------------------------------------------------------------ */

export const reviewCallSchema = z.object({
  call_id: z.string(),
  started_at: z.string(),
  /** Comma-separated: `escalated`, `negative_end` or both. */
  reason: z.string(),
  last_emotion: z.string().nullable(),
  duration_s: z.number().nullable(),
  turns: z.number(),
  transcript: z.array(z.object({ who: z.enum(['caller', 'agent']), text: z.string() })),
});
export type ReviewCall = z.infer<typeof reviewCallSchema>;

export const reviewCallsSchema = z.object({ calls: z.array(reviewCallSchema) });
export type InsightsReviewCalls = z.infer<typeof reviewCallsSchema>;
