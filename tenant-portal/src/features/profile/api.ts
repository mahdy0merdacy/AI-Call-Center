/**
 * PROPOSED endpoints (not in the backend docs yet). Kept in this one module so
 * the contract is easy to adjust once the backend ships them; MSW serves them
 * meanwhile (src/mocks/handlers/profile.ts).
 *
 *   PATCH /api/auth/me/               { full_name }                        -> user
 *   POST  /api/auth/change-password/  { current_password, new_password }   -> { access? }
 */
import { z } from 'zod';
import { currentUserSchema, type CurrentUser } from '@/features/auth/types';
import { api } from '@/lib/apiClient';

export async function updateProfile(fullName: string): Promise<CurrentUser> {
  const { data } = await api.patch<unknown>('/auth/me/', { full_name: fullName });
  return currentUserSchema.parse(data);
}

const changePasswordResponseSchema = z.object({ access: z.string().optional() }).passthrough();

/**
 * Other sessions are revoked server-side. If the backend also rotates this
 * session it returns a new access token (the refresh cookie is reset too).
 */
export async function changePassword(currentPassword: string, newPassword: string): Promise<string | null> {
  const { data } = await api.post<unknown>(
    '/auth/change-password/',
    { current_password: currentPassword, new_password: newPassword },
    { withCredentials: true },
  );
  const parsed = changePasswordResponseSchema.safeParse(data);
  return parsed.success ? (parsed.data.access ?? null) : null;
}
