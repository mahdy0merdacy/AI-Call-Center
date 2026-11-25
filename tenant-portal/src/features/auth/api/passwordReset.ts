import { api } from '@/lib/apiClient';
import { toApiError } from '@/lib/errors';

/** Always answers the same way, whether or not an account exists (anti-enumeration). */
export async function requestPasswordReset(email: string): Promise<void> {
  await api.post('/auth/password-reset/', { email }, { skipAuthRefresh: true });
}

/** On success the backend revokes every session of the account, everywhere. */
export async function confirmPasswordReset(uid: string, token: string, newPassword: string): Promise<void> {
  await api.post(
    '/auth/password-reset/confirm/',
    { uid, token, new_password: newPassword },
    { skipAuthRefresh: true },
  );
}

export type ResetLinkProblem = 'expired' | 'used' | 'invalid';

/**
 * Returns which link problem a confirm error means, or null when it is
 * something else (password validation, network…). Assumed codes: as for
 * email verification; a 400 carrying uid/token errors counts as invalid too.
 */
export function resetLinkProblem(error: unknown): ResetLinkProblem | null {
  const apiError = toApiError(error);
  if (apiError.hasCode('token_expired', 'link_expired', 'expired')) return 'expired';
  if (apiError.hasCode('token_used', 'already_used')) return 'used';
  if (
    apiError.hasCode('invalid_token', 'token_invalid', 'invalid_uid') ||
    'token' in apiError.fieldErrors ||
    'uid' in apiError.fieldErrors ||
    apiError.status === 404
  ) {
    return 'invalid';
  }
  return null;
}
