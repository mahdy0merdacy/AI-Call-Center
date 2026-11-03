import { HttpResponse, http } from 'msw';
import { db, endSession, findTenant, findUserById, rotateSession } from '../db';
import { mintAccessToken } from '../jwt';
import { apiUrl, authenticate, drfError, hasCsrfHeader, latency, serializeUser } from '../utils';

export const sessionHandlers = [
  http.post(apiUrl('/auth/refresh/'), async ({ request }) => {
    await latency();
    if (!hasCsrfHeader(request)) {
      return drfError(403, 'CSRF Failed: missing X-Requested-With header.', 'csrf_failed');
    }
    if (!db.refreshCookie) {
      return drfError(401, 'Refresh token not found.', 'not_authenticated');
    }
    const userId = rotateSession();
    const user = userId ? findUserById(userId) : undefined;
    if (!user) {
      // Reused (blacklisted) refresh token.
      endSession();
      return drfError(401, 'Token is blacklisted', 'token_not_valid');
    }
    if (findTenant(user.tenant_id)?.status === 'suspended') {
      endSession();
      return drfError(403, 'Your organization has been suspended.', 'tenant_suspended');
    }
    return HttpResponse.json({ access: mintAccessToken(user) });
  }),

  http.post(apiUrl('/auth/logout/'), async ({ request }) => {
    await latency();
    if (!hasCsrfHeader(request)) {
      return drfError(403, 'CSRF Failed: missing X-Requested-With header.', 'csrf_failed');
    }
    endSession();
    return new HttpResponse(null, { status: 205 });
  }),

  http.get(apiUrl('/auth/me/'), async ({ request }) => {
    await latency();
    const result = authenticate(request);
    if ('response' in result) return result.response;
    return HttpResponse.json(serializeUser(result.user));
  }),

  http.get(apiUrl('/tenant/me/'), async ({ request }) => {
    await latency();
    const result = authenticate(request);
    if ('response' in result) return result.response;
    const tenant = findTenant(result.user.tenant_id);
    if (!tenant) return drfError(404, 'No tenant for this account.', 'tenant_not_found');
    return HttpResponse.json(tenant);
  }),
];
