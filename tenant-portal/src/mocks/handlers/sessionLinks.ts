/**
 * Mock of the session-links endpoints of the documents service. Same rules as
 * the real one: tenant from the token, another tenant's link answers 404, and
 * `expires_at` must be a future datetime.
 */
import { HttpResponse, http } from 'msw';
import { z } from 'zod';
import { addTenantLink, findTenantLink, listTenantLinks, persistLinks } from '../sessionLinksStore';
import { drfError, latency } from '../utils';
import { documentsUrl, requireTenant } from './documents';

const createBody = z.object({ expires_at: z.string().nullable().optional() });

export const sessionLinksHandlers = [
  http.get(documentsUrl('/session-links/'), async ({ request }) => {
    await latency();
    const tenant = requireTenant(request);
    if ('response' in tenant) return tenant.response;

    const all = listTenantLinks(tenant.tenantId);
    return HttpResponse.json({ count: all.length, next: null, previous: null, results: all });
  }),

  http.post(documentsUrl('/session-links/'), async ({ request }) => {
    await latency();
    const tenant = requireTenant(request);
    if ('response' in tenant) return tenant.response;

    const body = createBody.safeParse(await request.json().catch(() => ({})));
    if (!body.success) return drfError(400, 'Invalid request.');

    let expiresAt: string | null = null;
    const raw = body.data.expires_at;
    if (raw) {
      const parsed = new Date(raw);
      if (Number.isNaN(parsed.getTime())) {
        return HttpResponse.json({ expires_at: ['Datetime has wrong format.'] }, { status: 400 });
      }
      if (parsed.getTime() <= Date.now()) {
        return HttpResponse.json({ expires_at: ['expires_at must be in the future.'] }, { status: 400 });
      }
      expiresAt = parsed.toISOString();
    }

    return HttpResponse.json(addTenantLink(tenant.tenantId, expiresAt), { status: 201 });
  }),

  http.post(documentsUrl('/session-links/:id/deactivate/'), async ({ request, params }) => {
    await latency();
    const tenant = requireTenant(request);
    if ('response' in tenant) return tenant.response;

    const found = findTenantLink(tenant.tenantId, String(params.id));
    if (!found) return drfError(404, 'No SessionLink matches the given query.');
    // Idempotent: deactivating an already-inactive link succeeds.
    if (found.is_active) {
      found.is_active = false;
      persistLinks();
    }
    return HttpResponse.json(found);
  }),
];
