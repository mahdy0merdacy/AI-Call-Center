import { api } from '@/lib/apiClient';
import { toApiError } from '@/lib/errors';

export interface RegisterPayload {
  companyName: string;
  firstName: string;
  lastName: string;
  email: string;
  password: string;
}

/**
 * The backend answers identically whether or not the email is already
 * registered (anti-enumeration), so success never means "account created".
 */
/**
 * The backend answers identically whether or not the email is already
 * registered (anti-enumeration), so success never means "account created".
 */
export async function registerRequest({
  companyName,
  firstName,
  lastName,
  email,
  password,
}: RegisterPayload): Promise<void> {
  await api.post(
    '/auth/register/',
    { company_name: companyName, first_name: firstName, last_name: lastName, email, password },
    { skipAuthRefresh: true },
  );
}

/** Always answers the same way, whether or not the address exists. */
export async function resendVerificationRequest(email: string): Promise<void> {
  await api.post('/auth/resend-verification/', { email }, { skipAuthRefresh: true });
}

async function verifyEmailRequest(uid: string, token: string): Promise<void> {
  await api.post('/auth/verify-email/', { uid, token }, { skipAuthRefresh: true });
}

const inflightVerifications = new Map<string, Promise<void>>();

/**
 * The link is single-use: React StrictMode (and a remount) must not send it
 * twice, or the second call would report "already used". Calls for the same
 * uid+token share one request.
 */
export function verifyEmailOnce(uid: string, token: string): Promise<void> {
  const key = `${uid}:${token}`;
  let request = inflightVerifications.get(key);
  if (!request) {
    request = verifyEmailRequest(uid, token);
    inflightVerifications.set(key, request);
  }
  return request;
}

export type VerifyFailure =
  | { kind: 'expired' }
  | { kind: 'used' }
  | { kind: 'invalid' }
  | { kind: 'transient'; message: string };

/** Assumed backend codes; anything else in 4xx is treated as an invalid link. */
export function classifyVerifyError(error: unknown): VerifyFailure {
  const apiError = toApiError(error);
  if (apiError.kind === 'network' || apiError.kind === 'timeout' || apiError.kind === 'server') {
    return { kind: 'transient', message: apiError.message };
  }
  if (apiError.hasCode('token_expired', 'link_expired', 'expired')) return { kind: 'expired' };
  if (apiError.hasCode('token_used', 'already_used', 'already_verified')) return { kind: 'used' };
  return { kind: 'invalid' };
}

export function forgetVerification(uid: string, token: string): void {
  inflightVerifications.delete(`${uid}:${token}`);
}
