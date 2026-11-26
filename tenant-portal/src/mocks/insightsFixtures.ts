/**
 * Deterministic fake analytics for the insights mock. Figures depend on the
 * tenant, the period and the `synthetic` filter (seeded, so a reload shows the
 * same numbers). Maison Verte has no calls, to demo the empty states.
 */
import type {
  InsightsActivity,
  InsightsKnowledge,
  InsightsReviewCalls,
  InsightsSentiment,
  InsightsSummary,
  InsightsTools,
  Kpis,
  ReviewCall,
  SyntheticMode,
} from '@/features/insights/types';

interface TenantProfile {
  /** Average real calls on a weekday. */
  dailyCalls: number;
  tools: string[];
  documents: string[];
  gaps: string[];
  calls: { reason: ReviewCall['reason']; emotion: string; transcript: ReviewCall['transcript'] }[];
}

const caller = (text: string) => ({ who: 'caller' as const, text });
const agent = (text: string) => ({ who: 'agent' as const, text });

const PROFILES: Record<string, TenantProfile> = {
  'acme-delivery': {
    dailyCalls: 46,
    tools: ['lookup_order', 'track_parcel', 'schedule_redelivery', 'update_address', 'create_ticket', 'transfer_to_human'],
    documents: ['delivery-faq.pdf', 'refund-policy.docx', 'shipping-rates.xlsx', 'returns-guide.pdf', 'opening-hours.txt'],
    gaps: [
      'Can I have my parcel delivered to a locker instead?',
      'Do you deliver on Sundays?',
      'How do I get an invoice with my company VAT number?',
      'Can the driver leave the parcel with my neighbour?',
      'What happens if I am not home for the second attempt?',
      'Do you ship to the Channel Islands?',
      'Is there a discount for sending more than ten parcels a month?',
      'Can I change the delivery date after the parcel has left the depot?',
    ],
    calls: [
      {
        reason: 'escalated',
        emotion: 'frustrated',
        transcript: [
          agent('Hello, Acme Delivery, how can I help?'),
          caller('My parcel was marked as delivered yesterday but I never got it.'),
          agent('I am sorry to hear that. Could you give me the tracking number?'),
          caller('It is AC 4471 9920.'),
          agent('Thank you. The driver scanned it at 14:32 at your front door.'),
          caller('There was nothing there. This is the second time this month.'),
          agent('I understand. I am transferring you to a colleague who can open a claim right away.'),
        ],
      },
      {
        reason: 'negative_end',
        emotion: 'angry',
        transcript: [
          agent('Hello, Acme Delivery, how can I help?'),
          caller('I want a refund for the delivery fee, the parcel arrived three days late.'),
          agent('I can see the delay. Delivery fees are refunded when a parcel is more than five days late.'),
          caller('That is ridiculous. I paid for express.'),
          agent('I am sorry. I can offer a voucher for your next express shipment.'),
          caller('Forget it.'),
        ],
      },
      {
        reason: 'escalated,negative_end',
        emotion: 'angry',
        transcript: [
          caller('I have been waiting for a callback for a week.'),
          agent('I am sorry about the wait. Could I have your order number?'),
          caller('I gave it three times already. Get me a person.'),
          agent('Of course, I am transferring you now.'),
        ],
      },
      {
        reason: 'escalated',
        emotion: 'confused',
        transcript: [
          caller('Hi, I need to send a pallet to Ireland, how much is it?'),
          agent('I can quote parcels up to 30 kg. For pallets, a colleague will help you.'),
          caller('Oh, okay. Yes please.'),
        ],
      },
      {
        reason: 'negative_end',
        emotion: 'sad',
        transcript: [
          caller('The flowers I sent for my mother arrived damaged.'),
          agent('I am really sorry. Could you send a photo through the link I am texting you?'),
          caller('Yes. It was her birthday, it is a shame.'),
          agent('I understand. Once we have the photo, the refund takes about three days.'),
        ],
      },
      {
        reason: 'escalated',
        emotion: 'anxious',
        transcript: [
          caller('My passport is in that parcel and I fly on Friday.'),
          agent('Let me check. The parcel is at the Leeds depot and is due tomorrow.'),
          caller('Tomorrow is too late if anything goes wrong. Can someone call the depot?'),
          agent('I am putting you through to the depot team now.'),
        ],
      },
    ],
  },
  'swiftnet-isp': {
    dailyCalls: 31,
    tools: ['check_outage', 'lookup_account', 'run_line_test', 'book_engineer', 'transfer_to_human'],
    documents: ['swiftnet-faq.pdf', 'router-setup.pdf', 'tariffs-2026.pdf'],
    gaps: [
      'Can I keep my landline number if I switch to fibre?',
      'Do you offer a static IP address?',
      'How do I set up parental controls on the new router?',
      'Is there a student discount?',
    ],
    calls: [
      {
        reason: 'escalated',
        emotion: 'frustrated',
        transcript: [
          caller('My internet has been down since this morning.'),
          agent('I can see an outage in your area. Engineers expect it to be fixed by 6 pm.'),
          caller('I work from home, I cannot wait that long.'),
          agent('I understand. Let me transfer you to see what we can offer in the meantime.'),
        ],
      },
      {
        reason: 'negative_end',
        emotion: 'angry',
        transcript: [
          caller('Why did my bill go up by ten pounds?'),
          agent('Your introductory offer ended last month.'),
          caller('Nobody told me. I want to cancel.'),
        ],
      },
    ],
  },
};

const SYNTHETIC_SHARE = 0.28;

function volumeFactor(synthetic: SyntheticMode): number {
  if (synthetic === 'exclude') return 1 - SYNTHETIC_SHARE;
  if (synthetic === 'only') return SYNTHETIC_SHARE;
  return 1;
}

/** mulberry32: tiny seeded PRNG. */
function random(seedText: string): () => number {
  let seed = 0;
  for (const char of seedText) seed = (Math.imul(seed, 31) + char.charCodeAt(0)) | 0;
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const DAY_MS = 86_400_000;
const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const round = (n: number, digits = 3) => Math.round(n * 10 ** digits) / 10 ** digits;
/** 0 = Monday, like the API. */
const weekdayOf = (ms: number) => (new Date(ms).getUTCDay() + 6) % 7;
const WEEKDAY_WEIGHT = [1.15, 1.05, 1, 1, 1.1, 0.6, 0.35];

function todayStart(): number {
  const now = new Date();
  return Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
}

interface Params {
  tenantId: string;
  days: number;
  synthetic: SyntheticMode;
}

function emptyKpis(): Kpis {
  return {
    calls: 0,
    self_service_rate: null,
    escalation_rate: null,
    avg_duration_s: null,
    tool_success_rate: null,
    turn_p50_ms: null,
    escalated_calls: 0,
  };
}

function periodDaily(profile: TenantProfile, { tenantId, days, synthetic }: Params, endMs: number, salt: string) {
  const rand = random(`${tenantId}:${days}:${synthetic}:${salt}`);
  const factor = volumeFactor(synthetic);
  return Array.from({ length: days }, (_, i) => {
    const dayMs = endMs - (days - 1 - i) * DAY_MS;
    const calls = Math.round(profile.dailyCalls * (WEEKDAY_WEIGHT[weekdayOf(dayMs)] ?? 1) * factor * (0.75 + rand() * 0.5));
    const escalated = Math.round(calls * (0.08 + rand() * 0.08));
    return {
      day: isoDay(dayMs),
      calls,
      escalated_calls: escalated,
      self_service_rate: calls > 0 ? round((calls - escalated) / calls - rand() * 0.04) : null,
    };
  });
}

function kpisOf(daily: InsightsSummary['daily'], rand: () => number, drift: number): Kpis {
  const calls = daily.reduce((sum, d) => sum + d.calls, 0);
  if (calls === 0) return emptyKpis();
  const escalated = daily.reduce((sum, d) => sum + d.escalated_calls, 0);
  const selfService = daily.reduce((sum, d) => sum + (d.self_service_rate ?? 0) * d.calls, 0) / calls;
  return {
    calls,
    self_service_rate: round(selfService - drift),
    escalation_rate: round(escalated / calls),
    avg_duration_s: round(140 + rand() * 50 + drift * 200, 1),
    tool_success_rate: round(0.93 + rand() * 0.05 - drift),
    turn_p50_ms: Math.round(720 + rand() * 260 + drift * 1500),
    escalated_calls: escalated,
  };
}

export function summaryFixture(params: Params): InsightsSummary {
  const profile = PROFILES[params.tenantId];
  const end = todayStart();
  const start = end - (params.days - 1) * DAY_MS;
  const base = {
    tenants: [params.tenantId],
    range: { from: isoDay(start), to: isoDay(end), days: params.days },
  };
  if (!profile) return { ...base, kpis: emptyKpis(), previous: emptyKpis(), daily: [], updated_at: null };

  const daily = periodDaily(profile, params, end, 'current');
  const previousDaily = periodDaily(profile, params, start - DAY_MS, 'previous');
  const rand = random(`${params.tenantId}:${params.days}:${params.synthetic}:kpis`);
  return {
    ...base,
    kpis: kpisOf(daily, rand, 0),
    // The previous period did a little worse: the agent is improving.
    previous: kpisOf(previousDaily, rand, 0.03),
    daily,
    updated_at: new Date(Date.now() - 4 * 60_000).toISOString(),
  };
}

const EMOTION_WEIGHTS: [string, number][] = [
  ['neutral', 0.46],
  ['satisfied', 0.24],
  ['confused', 0.1],
  ['frustrated', 0.09],
  ['anxious', 0.05],
  ['angry', 0.04],
  ['sad', 0.02],
];

export function sentimentFixture(params: Params): InsightsSentiment {
  const summary = summaryFixture(params);
  const total = summary.kpis.calls;
  if (total === 0) {
    return {
      emotions: [],
      daily: [],
      calls: { total: 0, escalation_risk: 0, improving: 0, stable: 0, worsening: 0, ended_negative: 0 },
    };
  }
  const rand = random(`${params.tenantId}:${params.days}:${params.synthetic}:sentiment`);
  const utterances = total * 7;
  const emotions = EMOTION_WEIGHTS.map(([emotion, weight]) => {
    const n = Math.round(utterances * weight * (0.85 + rand() * 0.3));
    return { emotion, utterances: n };
  });
  const sum = emotions.reduce((acc, e) => acc + e.utterances, 0);
  const improving = Math.round(total * 0.22);
  const worsening = Math.round(total * 0.09);
  return {
    emotions: emotions.map((e) => ({ ...e, share: round(e.utterances / sum, 4) })),
    daily: summary.daily.map((d) => ({
      day: d.day,
      ...Object.fromEntries(EMOTION_WEIGHTS.map(([emotion, weight]) => [emotion, Math.round(d.calls * 7 * weight)])),
    })),
    calls: {
      total,
      escalation_risk: Math.round(total * 0.06),
      improving,
      stable: total - improving - worsening,
      worsening,
      ended_negative: Math.round(total * 0.07),
    },
  };
}

export function toolsFixture(params: Params): InsightsTools {
  const profile = PROFILES[params.tenantId];
  const calls = summaryFixture(params).kpis.calls;
  if (!profile || calls === 0) return { tools: [] };
  const rand = random(`${params.tenantId}:${params.days}:${params.synthetic}:tools`);
  return {
    tools: profile.tools.map((tool, i) => {
      const used = Math.max(1, Math.round(calls * (0.9 / (i + 1)) * (0.8 + rand() * 0.4)));
      // One tool (a flaky third-party API) fails noticeably more than the others.
      const errorRate = i === profile.tools.length - 2 ? 0.12 : 0.01 + rand() * 0.03;
      const errors = Math.round(used * errorRate);
      const timeouts = Math.round(used * rand() * 0.015);
      const ok = used - errors - timeouts;
      return {
        tool,
        calls: used,
        ok,
        errors,
        timeouts,
        success_rate: round(ok / used),
        avg_latency_ms: Math.round(180 + rand() * 900),
      };
    }),
  };
}

export function knowledgeFixture(params: Params): InsightsKnowledge {
  const profile = PROFILES[params.tenantId];
  const calls = summaryFixture(params).kpis.calls;
  if (!profile || calls === 0) {
    return { searches: 0, covered: 0, uncovered: 0, failed: 0, coverage_rate: null, documents: [], gaps: [] };
  }
  const rand = random(`${params.tenantId}:${params.days}:${params.synthetic}:knowledge`);
  const searches = Math.round(calls * 1.4);
  const failed = Math.round(searches * 0.01);
  const uncovered = Math.round(searches * (0.14 + rand() * 0.06));
  const covered = searches - uncovered - failed;
  const end = todayStart();
  // Short periods only surface the most frequent questions.
  const gapCount = Math.min(profile.gaps.length, params.days <= 7 ? 4 : params.days <= 30 ? 6 : profile.gaps.length);
  return {
    searches,
    covered,
    uncovered,
    failed,
    coverage_rate: round(covered / searches),
    documents: profile.documents.map((title, i) => ({
      title,
      uses: Math.max(1, Math.round((covered * 0.55) / (i + 1) ** 1.1)),
    })),
    gaps: profile.gaps.slice(0, gapCount).map((question, i) => ({
      question,
      asked: Math.max(1, Math.round((uncovered * 0.35) / (i + 1.5))),
      last_day: isoDay(end - Math.floor(rand() * Math.min(params.days, 6)) * DAY_MS),
    })),
  };
}

/** Business hours in UTC (customers are in Europe), with a lunch dip. */
const HOUR_WEIGHT = [
  0, 0, 0, 0, 0, 0.05, 0.2, 0.6, 1, 1.2, 1.15, 1.05, 0.75, 0.85, 1.1, 1.1, 1, 0.85, 0.55, 0.3, 0.15, 0.08, 0.02, 0,
];

export function activityFixture(params: Params): InsightsActivity {
  const summary = summaryFixture(params);
  if (summary.kpis.calls === 0) return { heatmap: [] };
  const rand = random(`${params.tenantId}:${params.days}:${params.synthetic}:activity`);
  const weightSum = HOUR_WEIGHT.reduce((a, b) => a + b, 0);
  const perWeekday = summary.kpis.calls / 7;
  const heatmap: InsightsActivity['heatmap'] = [];
  for (let weekday = 0; weekday < 7; weekday += 1) {
    for (let hour = 0; hour < 24; hour += 1) {
      const expected = (perWeekday * (WEEKDAY_WEIGHT[weekday] ?? 1) * (HOUR_WEIGHT[hour] ?? 0)) / weightSum;
      const calls = Math.round(expected * (0.7 + rand() * 0.6));
      if (calls > 0) heatmap.push({ weekday, hour, calls });
    }
  }
  return { heatmap };
}

export function reviewCallsFixture(params: Params, limit: number): InsightsReviewCalls {
  const profile = PROFILES[params.tenantId];
  const summary = summaryFixture(params);
  if (!profile || summary.kpis.calls === 0) return { calls: [] };
  const rand = random(`${params.tenantId}:${params.days}:${params.synthetic}:calls`);
  const count = Math.min(limit, Math.max(2, Math.round(summary.kpis.escalated_calls / 6)), 20);
  const now = Date.now();
  const calls = Array.from({ length: count }, (_, i): ReviewCall => {
    const template = profile.calls[i % profile.calls.length]!;
    // Spread over the period, newest first.
    const startedAt = now - Math.round(((i + rand()) / count) * params.days * DAY_MS * 0.95);
    return {
      call_id: `mock-${params.tenantId}-${params.days}-${i}`,
      started_at: new Date(startedAt).toISOString(),
      reason: template.reason,
      last_emotion: template.emotion,
      duration_s: Math.round(90 + rand() * 300),
      turns: template.transcript.length,
      transcript: template.transcript,
    };
  });
  return { calls };
}
