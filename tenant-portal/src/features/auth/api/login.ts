import { z } from 'zod';
import { api, extractAccessToken } from '@/lib/apiClient';
import { toApiError, type ApiRequestError } from '@/lib/errors';

/* ------------------------------------------------------------------ */
/* Requests                                                            */
/* ------------------------------------------------------------------ */

const mfaRequiredSchema = z.object({
  mfa_required: z.literal(true),
  mfa_token: z.string().min(1),
  /** Seconds the challenge stays valid; the contract says 5 minutes. */
  expires_in: z.number().positive().optional(),
});

export type LoginResult =
  | { kind: 'authenticated'; accessToken: string }
  | { kind: 'mfa_required'; mfaToken: string; expiresInSeconds: number };

export const MFA_CHALLENGE_SECONDS = 5 * 60;
export const MFA_MAX_ATTEMPTS = 5;

export async function loginRequest(email: string, password: string): Promise<LoginResult> {
  // withCredentials so the refresh cookie set by the response is stored.
  const { data } = await api.post<unknown>(
    '/auth/login/',
    { email, password },
    { withCredentials: true, skipAuthRefresh: true },
  );
  const mfa = mfaRequiredSchema.safeParse(data);
  if (mfa.success) {
    return {
      kind: 'mfa_required',
      mfaToken: mfa.data.mfa_token,
      expiresInSeconds: mfa.data.expires_in ?? MFA_CHALLENGE_SECONDS,
    };
  }
  return { kind: 'authenticated', accessToken: extractAccessToken(data) };
}

/** `code` is a 6-digit TOTP or a backup code; the backend accepts both. */
export async function mfaVerifyRequest(mfaToken: string, code: string): Promise<string> {
  const { data } = await api.post<unknown>(
    '/auth/mfa/verify/',
    { mfa_token: mfaToken, code },
    { withCredentials: true, skipAuthRefresh: true },
  );
  return extractAccessToken(data);
}

/* ------------------------------------------------------------------ */
/* Error classification                                                */
/*                                                                     */
/* The codes below are the frontend's assumption of the backend's      */
/* error codes. Unknown codes fall back to safe generic handling.      */
/* ------------------------------------------------------------------ */

const LOCKOUT_CODES = ['account_locked', 'ip_blocked', 'axes_locked', 'locked_out', 'lockout'];

export type LoginFailure =
  | { kind: 'locked'; retryAfterSeconds: number | null }
  | { kind: 'network'; message: string }
  | { kind: 'server'; message: string }
  /** Wrong password, unknown email, unverified email, suspended tenant: never distinguished. */
  | { kind: 'invalid' };

export function classifyLoginError(error: unknown): LoginFailure {
  const apiError = toApiError(error);
  if (apiError.kind === 'rate_limited' || apiError.hasCode(...LOCKOUT_CODES)) {
    return { kind: 'locked', retryAfterSeconds: apiError.retryAfterSeconds };
  }
  if (apiError.kind === 'network' || apiError.kind === 'timeout') {
    return { kind: 'network', message: apiError.message };
  }
  if (apiError.kind === 'server') return { kind: 'server', message: apiError.message };
  return { kind: 'invalid' };
}

export type MfaFailure =
  | { kind: 'invalid'; attemptsRemaining: number | null }
  | { kind: 'replay' }
  | { kind: 'expired' }
  | { kind: 'exhausted' }
  | { kind: 'throttled'; retryAfterSeconds: number | null }
  | { kind: 'other'; message: string };

function attemptsFrom(error: ApiRequestError): number | null {
  const value = error.body?.attempts_remaining;
  return typeof value === 'number' ? value : null;
}

export function classifyMfaError(error: unknown): MfaFailure {
  const apiError = toApiError(error);
  if (apiError.kind === 'rate_limited') {
    return { kind: 'throttled', retryAfterSeconds: apiError.retryAfterSeconds };
  }
  if (apiError.hasCode('mfa_attempts_exceeded', 'too_many_attempts', 'challenge_locked')) {
    return { kind: 'exhausted' };
  }
  if (
    apiError.hasCode('mfa_challenge_expired', 'mfa_token_expired', 'mfa_token_invalid') ||
    apiError.status === 401
  ) {
    return { kind: 'expired' };
  }
  if (apiError.hasCode('code_already_used', 'otp_reused', 'replay')) return { kind: 'replay' };
  if (apiError.kind === 'validation' || apiError.hasCode('invalid_code', 'invalid_otp')) {
    return { kind: 'invalid', attemptsRemaining: attemptsFrom(apiError) };
  }
  return { kind: 'other', message: apiError.message };
}
