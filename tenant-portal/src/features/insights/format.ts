import { locale, messages } from '@/i18n';

const LANGUAGE_TAG = locale === 'fr' ? 'fr-FR' : 'en-GB';
export const EMPTY_VALUE = '—';

const integer = new Intl.NumberFormat(LANGUAGE_TAG, { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat(LANGUAGE_TAG, { maximumFractionDigits: 1 });
const signedOneDecimal = new Intl.NumberFormat(LANGUAGE_TAG, { maximumFractionDigits: 1, signDisplay: 'exceptZero' });
const percent = new Intl.NumberFormat(LANGUAGE_TAG, { style: 'percent', maximumFractionDigits: 1 });
const signedPercent = new Intl.NumberFormat(LANGUAGE_TAG, {
  style: 'percent',
  maximumFractionDigits: 0,
  signDisplay: 'exceptZero',
});

export const formatInt = (n: number): string => integer.format(n);

/** 0..1 -> "42.5 %" (locale-aware); null -> "—". */
export const formatRate = (rate: number | null): string => (rate === null ? EMPTY_VALUE : percent.format(rate));

/** 185 -> "3 min 05 s", 42 -> "42 s". */
export function formatDurationSeconds(seconds: number | null): string {
  if (seconds === null) return EMPTY_VALUE;
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes > 0 ? `${minutes} min ${String(rest).padStart(2, '0')} s` : `${rest} s`;
}

/** 640 -> "640 ms", 1240 -> "1.2 s". */
export function formatMs(ms: number | null): string {
  if (ms === null) return EMPTY_VALUE;
  return ms < 1000 ? `${Math.round(ms)} ms` : `${oneDecimal.format(ms / 1000)} s`;
}

const shortDay = new Intl.DateTimeFormat(LANGUAGE_TAG, { day: 'numeric', month: 'short', timeZone: 'UTC' });
const longDay = new Intl.DateTimeFormat(LANGUAGE_TAG, { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
const dateTime = new Intl.DateTimeFormat(LANGUAGE_TAG, { dateStyle: 'medium', timeStyle: 'short' });

function parseDay(day: string): Date | null {
  // "2026-11-26" is a UTC calendar day, not an instant.
  const date = new Date(`${day.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDay(day: string, long = false): string {
  const date = parseDay(day);
  if (!date) return day;
  return (long ? longDay : shortDay).format(date);
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? EMPTY_VALUE : dateTime.format(date);
}

/** Short weekday names, Monday first (the API's weekday 0). */
export const WEEKDAYS: string[] = Array.from({ length: 7 }, (_, i) =>
  // 2024-01-01 was a Monday.
  new Intl.DateTimeFormat(LANGUAGE_TAG, { weekday: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, 1 + i))),
);

/* ------------------------------------------------------------------ */
/* Change vs the previous period                                       */
/* ------------------------------------------------------------------ */

/** Which way is good for a KPI; `none` for volumes that are neither. */
export type Better = 'up' | 'down' | 'none';
export type Tone = 'good' | 'bad' | 'neutral';

export interface Change {
  text: string;
  tone: Tone;
  direction: 'up' | 'down' | 'flat';
}

const directionOf = (diff: number): Change['direction'] => (diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat');

function toneOf(diff: number, better: Better): Tone {
  if (diff === 0 || better === 'none') return 'neutral';
  return (diff > 0) === (better === 'up') ? 'good' : 'bad';
}

/** Rates change in percentage points: 0.42 -> 0.45 is "+3 pts". */
export function rateChange(current: number | null, previous: number | null, better: Better): Change | null {
  if (current === null || previous === null) return null;
  const points = Math.round((current - previous) * 1000) / 10;
  if (points === 0) return { text: messages.insights.kpis.unchanged, tone: 'neutral', direction: 'flat' };
  return {
    text: messages.insights.kpis.points(signedOneDecimal.format(points)),
    tone: toneOf(points, better),
    direction: directionOf(points),
  };
}

/** Counts and durations change relatively: 120 -> 150 is "+25 %". */
export function relativeChange(current: number | null, previous: number | null, better: Better): Change | null {
  if (current === null || previous === null || previous === 0) return null;
  const ratio = (current - previous) / previous;
  if (Math.abs(ratio) < 0.005) return { text: messages.insights.kpis.unchanged, tone: 'neutral', direction: 'flat' };
  return { text: signedPercent.format(ratio), tone: toneOf(ratio, better), direction: directionOf(ratio) };
}
