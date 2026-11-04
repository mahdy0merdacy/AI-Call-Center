/**
 * Mock of the documents service (services/documents). It answers like the real
 * one: bearer token required, `tenant_admin` only (403 otherwise), and the
 * tenant always comes from the token, never from the request.
 */
import { HttpResponse, http } from 'msw';
import { env } from '@/lib/env';
import { listTenantDocuments } from '../documentsStore';
import { authenticate, drfError, latency } from '../utils';

const documentsUrl = (path: string): string => `${env.documentsRoot}${path}`;

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

function toInt(value: string | null, fallback: number): number {
  const parsed = Number.parseInt(value ?? '', 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

export const documentsHandlers = [
  http.get(documentsUrl('/documents/'), async ({ request }) => {
    await latency();
    const result = authenticate(request);
    if ('response' in result) return result.response;
    const { user } = result;

    if (user.role !== 'tenant_admin') {
      return drfError(403, 'This account role cannot use the documents service.');
    }
    if (user.tenant_id === null) {
      return drfError(401, 'Token does not carry a valid tenant.');
    }

    // DRF LimitOffsetPagination: ?limit= (default 50, max 200) and ?offset=.
    const url = new URL(request.url);
    const limit = Math.min(Math.max(toInt(url.searchParams.get('limit'), DEFAULT_LIMIT), 1), MAX_LIMIT);
    const offset = toInt(url.searchParams.get('offset'), 0);
    const all = listTenantDocuments(user.tenant_id);

    return HttpResponse.json({
      count: all.length,
      next: null,
      previous: null,
      results: all.slice(offset, offset + limit),
    });
  }),
];
