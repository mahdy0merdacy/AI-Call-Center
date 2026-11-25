import { api, cookieAuthConfig } from '@/lib/apiClient';
import { currentUserSchema, tenantSchema, type CurrentUser, type Tenant } from '../types';

export async function fetchCurrentUser(): Promise<CurrentUser> {
  const { data } = await api.get<unknown>('/auth/me/');
  return currentUserSchema.parse(data);
}

export async function fetchCurrentTenant(): Promise<Tenant> {
  const { data } = await api.get<unknown>('/tenant/me/');
  return tenantSchema.parse(data);
}

/** Blacklists the refresh token server-side and deletes the cookie. */
export async function logoutRequest(): Promise<void> {
  await api.post('/auth/logout/', null, { ...cookieAuthConfig, skipAuthRefresh: true });
}
