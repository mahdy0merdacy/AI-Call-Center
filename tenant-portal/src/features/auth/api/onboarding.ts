import { api, extractAccessToken } from '@/lib/apiClient';

/**
 * Google users only: creates their company. The response carries new tokens
 * whose claims include the tenant_id, so they replace the current ones.
 */
export async function onboardingRequest(companyName: string): Promise<string> {
  const { data } = await api.post<unknown>('/auth/onboarding/', { company_name: companyName }, { withCredentials: true });
  return extractAccessToken(data);
}
