import axios from 'axios';
import { messages } from '@/i18n';

export type ApiErrorKind =
  | 'network'
  | 'timeout'
  | 'rate_limited'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'validation'
  | 'server'
  | 'unknown';

export type FieldErrors = Record<string, string[]>;

/**
 * Normalised shape of every failed API call. Interceptors reject with this, so
 * components never need to understand axios or DRF error bodies.
 */
export class ApiRequestError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** Machine-readable code (`code`, `error` or `error_code` in the body). */
  readonly code: string | null;
  /** `{field: [messages]}` from DRF validation errors. */
  readonly fieldErrors: FieldErrors;
  /** `non_field_errors` and `detail` messages. */
  readonly formErrors: string[];
  /** From the Retry-After header or the DRF throttle message. */
  readonly retryAfterSeconds: number | null;
  /** Raw body for feature-specific fields (e.g. lockout metadata). Never logged. */
  readonly body: Record<string, unknown> | null;

  constructor(init: {
    kind: ApiErrorKind;
    status: number | null;
    code: string | null;
    message: string;
    fieldErrors?: FieldErrors;
    formErrors?: string[];
    retryAfterSeconds?: number | null;
    body?: Record<string, unknown> | null;
  }) {
    super(init.message);
    this.name = 'ApiRequestError';
    this.kind = init.kind;
    this.status = init.status;
    this.code = init.code;
    this.fieldErrors = init.fieldErrors ?? {};
    this.formErrors = init.formErrors ?? [];
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
    this.body = init.body ?? null;
  }

  hasCode(...codes: string[]): boolean {
    return this.code !== null && codes.includes(this.code);
  }
}

const NON_FIELD_KEYS = new Set(['non_field_errors', 'detail', '__all__']);
const META_KEYS = new Set(['code', 'error', 'error_code', 'status_code', 'retry_after']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Flattens DRF's string | string[] | nested-object error values into messages. */
function collectMessages(value: unknown): string[] {
  if (typeof value === 'string') return [value];
  if (Array.isArray(value)) return value.flatMap(collectMessages);
  if (isRecord(value)) {
    if (typeof value.message === 'string') return [value.message];
    return Object.values(value).flatMap(collectMessages);
  }
  return [];
}

function readCode(body: Record<string, unknown>): string | null {
  for (const key of ['code', 'error_code', 'error'] as const) {
    const value = body[key];
    if (typeof value === 'string' && /^[a-z0-9_.-]+$/i.test(value)) return value;
  }
  // DRF's `detail` may be an object like {detail: {code, message}} in custom handlers.
  const detail = body.detail;
  if (isRecord(detail) && typeof detail.code === 'string') return detail.code;
  return null;
}

function parseRetryAfter(header: unknown, body: Record<string, unknown> | null): number | null {
  if (typeof header === 'string' && header.trim() !== '') {
    const seconds = Number(header);
    if (Number.isFinite(seconds)) return Math.max(0, Math.ceil(seconds));
    const date = Date.parse(header);
    if (!Number.isNaN(date)) return Math.max(0, Math.ceil((date - Date.now()) / 1000));
  }
  if (body) {
    const explicit = body.retry_after;
    if (typeof explicit === 'number' && Number.isFinite(explicit)) return Math.ceil(explicit);
    // DRF throttling: "Request was throttled. Expected available in 42 seconds."
    if (typeof body.detail === 'string') {
      const match = /available in (\d+)/i.exec(body.detail);
      if (match?.[1]) return Number(match[1]);
    }
  }
  return null;
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 400 || status === 422) return 'validation';
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 429) return 'rate_limited';
  if (status >= 500) return 'server';
  return 'unknown';
}

function defaultMessage(kind: ApiErrorKind, retryAfter: number | null): string {
  switch (kind) {
    case 'network':
      return messages.errors.network;
    case 'timeout':
      return messages.errors.timeout;
    case 'rate_limited':
      return retryAfter ? messages.errors.rateLimitedFor(retryAfter) : messages.errors.rateLimited;
    case 'forbidden':
      return messages.errors.forbidden;
    case 'not_found':
      return messages.errors.notFound;
    case 'server':
      return messages.errors.server;
    case 'unauthorized':
      return messages.errors.sessionExpired;
    case 'validation':
    case 'unknown':
      return messages.errors.unknown;
  }
}

export function toApiError(error: unknown): ApiRequestError {
  if (error instanceof ApiRequestError) return error;

  if (axios.isAxiosError(error)) {
    if (!error.response) {
      const kind: ApiErrorKind =
        error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT' ? 'timeout' : 'network';
      return new ApiRequestError({
        kind,
        status: null,
        code: error.code ?? null,
        message: defaultMessage(kind, null),
      });
    }

    const { status, data, headers } = error.response;
    const body = isRecord(data) ? data : null;
    const kind = kindForStatus(status);
    const retryAfterSeconds = parseRetryAfter(
      (headers as Record<string, unknown>)['retry-after'],
      body,
    );

    const fieldErrors: FieldErrors = {};
    const formErrors: string[] = [];
    if (body) {
      for (const [key, value] of Object.entries(body)) {
        if (META_KEYS.has(key)) continue;
        const list = collectMessages(value);
        if (list.length === 0) continue;
        if (NON_FIELD_KEYS.has(key)) formErrors.push(...list);
        else fieldErrors[key] = list;
      }
    } else if (Array.isArray(data)) {
      formErrors.push(...collectMessages(data));
    }

    return new ApiRequestError({
      kind,
      status,
      code: body ? readCode(body) : null,
      message: formErrors[0] ?? defaultMessage(kind, retryAfterSeconds),
      fieldErrors,
      formErrors,
      retryAfterSeconds,
      body,
    });
  }

  return new ApiRequestError({
    kind: 'unknown',
    status: null,
    code: null,
    message: messages.errors.unknown,
  });
}

/**
 * Message safe to show in a toast/banner when there is no feature-specific
 * mapping. Network, timeout, throttling and 5xx get friendly copy; other
 * errors show the server's first form message if any.
 */
export function userMessage(error: unknown): string {
  const apiError = toApiError(error);
  if (apiError.kind === 'validation' || apiError.kind === 'unknown') {
    return apiError.formErrors[0] ?? defaultMessage(apiError.kind, null);
  }
  return defaultMessage(apiError.kind, apiError.retryAfterSeconds);
}
