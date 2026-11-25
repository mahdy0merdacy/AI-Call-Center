/**
 * Google sign-in through django-allauth:
 *   1. startGoogleRedirect() sends the browser to allauth (full page navigation).
 *   2. Google → allauth → back to /auth/google/callback with an allauth session cookie.
 *   3. exchangeGoogleSession() trades that session for our JWTs (within 5 minutes).
 */
import { z } from 'zod';
import { paths } from '@/app/paths';
import { api, cookieAuthConfig, extractAccessToken } from '@/lib/apiClient';
import { env } from '@/lib/env';
import { toApiError } from '@/lib/errors';

const RETURN_TO_KEY = 'tp-google-return-to';

export function googleCallbackUrl(): string {
  return `${window.location.origin}${paths.googleCallback}`;
}

function readCookie(name: string): string | null {
  const match = document.cookie.split('; ').find((entry) => entry.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

/**
 * Leaves the app for Google. `returnTo` (a path, not a secret) is kept in
 * sessionStorage so the user lands where they were heading afterwards.
 */
export function rememberGoogleReturnTo(returnTo?: string): void {
  try {
    if (returnTo) window.sessionStorage.setItem(RETURN_TO_KEY, returnTo);
    else window.sessionStorage.removeItem(RETURN_TO_KEY);
  } catch {
    // Non-critical: the user lands on the dashboard instead.
  }
}

export function startGoogleRedirect(returnTo?: string): void {
  rememberGoogleReturnTo(returnTo);

  if (env.googleAuthMethod === 'get') {
    const url = new URL(env.googleAuthUrl, window.location.origin);
    url.searchParams.set('process', 'login');
    url.searchParams.set('next', googleCallbackUrl());
    window.location.assign(url.toString());
    return;
  }

  // allauth headless: POST provider/callback_url/process as a regular form.
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = env.googleAuthUrl;
  const fields: Record<string, string> = {
    provider: 'google',
    callback_url: googleCallbackUrl(),
    process: 'login',
  };
  // Django's CSRF cookie is only readable when the API shares our origin.
  const csrf = readCookie('csrftoken');
  if (csrf) fields.csrfmiddlewaretoken = csrf;
  for (const [name, value] of Object.entries(fields)) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
}

export function consumeGoogleReturnTo(): string | null {
  try {
    const value = window.sessionStorage.getItem(RETURN_TO_KEY);
    window.sessionStorage.removeItem(RETURN_TO_KEY);
    return value;
  } catch {
    return null;
  }
}

const exchangeMetaSchema = z
  .object({
    onboarding_required: z.boolean().optional(),
    needs_onboarding: z.boolean().optional(),
    tenant_id: z.union([z.string(), z.number()]).nullable().optional(),
  })
  .passthrough();

export interface GoogleExchangeResult {
  accessToken: string;
  /** null when the backend doesn't say; the RequireCompleteAccount guard then decides. */
  onboardingRequired: boolean | null;
}

async function exchange(): Promise<GoogleExchangeResult> {
  const { data } = await api.post<unknown>('/auth/google/exchange/', null, {
    ...cookieAuthConfig,
    skipAuthRefresh: true,
  });
  const accessToken = extractAccessToken(data);
  const meta = exchangeMetaSchema.safeParse(data);
  let onboardingRequired: boolean | null = null;
  if (meta.success) {
    onboardingRequired =
      meta.data.onboarding_required ??
      meta.data.needs_onboarding ??
      (meta.data.tenant_id === undefined ? null : meta.data.tenant_id === null);
  }
  return { accessToken, onboardingRequired };
}

let inflightExchange: Promise<GoogleExchangeResult> | null = null;

/**
 * The allauth session is consumed by the exchange, so StrictMode's double
 * mount must not send it twice. Google returns with a full page load, which
 * resets this module, so one cached promise per load is enough.
 */
export function exchangeGoogleSessionOnce(): Promise<GoogleExchangeResult> {
  inflightExchange ??= exchange();
  return inflightExchange;
}

export type GoogleFailure =
  | { kind: 'conflict' }
  | { kind: 'expired' }
  | { kind: 'unverified' }
  | { kind: 'cancelled' }
  | { kind: 'failed'; message: string };

/** Assumed codes besides the documented `email_already_registered`. */
export function classifyGoogleError(error: unknown): GoogleFailure {
  const apiError = toApiError(error);
  if (apiError.hasCode('email_already_registered')) return { kind: 'conflict' };
  if (apiError.hasCode('email_not_verified', 'unverified_email')) return { kind: 'unverified' };
  if (
    apiError.hasCode('google_session_expired', 'social_session_expired', 'session_expired', 'no_social_session') ||
    apiError.status === 401
  ) {
    return { kind: 'expired' };
  }
  return { kind: 'failed', message: apiError.message };
}

/** allauth appends `?error=…` to the callback URL when the provider step fails. */
export function failureFromCallbackParams(params: URLSearchParams): GoogleFailure | null {
  const error = params.get('error');
  if (!error) return null;
  if (['cancelled', 'canceled', 'access_denied', 'denied'].includes(error)) return { kind: 'cancelled' };
  return { kind: 'failed', message: '' };
}
