/**
 * Mock of the documents service (services/documents). It answers like the real
 * one: bearer token required, `tenant_admin` only (403 otherwise), and the
 * tenant always comes from the token, never from the request.
 *
 * Upload checks mirror documents/validators.py (extension, empty, size). The
 * real service also sniffs the file's content; the mock does not.
 */
import { HttpResponse, http } from 'msw';
import { env } from '@/lib/env';
import { addTenantDocument, listTenantDocuments } from '../documentsStore';
import { authenticate, drfError, latency } from '../utils';

export const documentsUrl = (path: string): string => `${env.documentsRoot}${path}`;

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;
const MAX_FILENAME_LENGTH = 255;

/** extension -> canonical content type (the real service never trusts the client's). */
const ALLOWED_TYPES: Record<string, string> = {
  '.pdf': 'application/pdf',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.csv': 'text/csv',
  '.txt': 'text/plain',
};

function toInt(value: string | null, fallback: number): number {
  const parsed = Number.parseInt(value ?? '', 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

type TenantResult = { tenantId: string } | { response: Response };

/** Same gate as the real service: valid token, `tenant_admin` role, a tenant in the token. */
export function requireTenant(request: Request): TenantResult {
  const result = authenticate(request);
  if ('response' in result) return result;
  const { user } = result;
  if (user.role !== 'tenant_admin') {
    return { response: drfError(403, 'This account role cannot use the documents service.') };
  }
  if (user.tenant_id === null) {
    return { response: drfError(401, 'Token does not carry a valid tenant.') };
  }
  return { tenantId: user.tenant_id };
}

/** DRF puts upload problems under the field name: `{"file": ["..."]}`. */
const fileError = (message: string) => HttpResponse.json({ file: [message] }, { status: 400 });

export const documentsHandlers = [
  http.get(documentsUrl('/documents/'), async ({ request }) => {
    await latency();
    const tenant = requireTenant(request);
    if ('response' in tenant) return tenant.response;

    // DRF LimitOffsetPagination: ?limit= (default 50, max 200) and ?offset=.
    const url = new URL(request.url);
    const limit = Math.min(Math.max(toInt(url.searchParams.get('limit'), DEFAULT_LIMIT), 1), MAX_LIMIT);
    const offset = toInt(url.searchParams.get('offset'), 0);
    const all = listTenantDocuments(tenant.tenantId);

    return HttpResponse.json({
      count: all.length,
      next: null,
      previous: null,
      results: all.slice(offset, offset + limit),
    });
  }),

  http.post(documentsUrl('/documents/'), async ({ request }) => {
    await latency();
    const tenant = requireTenant(request);
    if ('response' in tenant) return tenant.response;

    const form = await request.formData().catch(() => null);
    const file = form?.get('file');
    if (!(file instanceof File)) return fileError('No file was submitted.');

    const filename = file.name.split(/[\\/]/).pop() ?? '';
    if (!filename) return fileError('The uploaded file has no filename.');
    if (filename.length > MAX_FILENAME_LENGTH) {
      return fileError(`Filename is too long (${filename.length} chars; max ${MAX_FILENAME_LENGTH}).`);
    }

    const dot = filename.lastIndexOf('.');
    const ext = dot >= 0 ? filename.slice(dot).toLowerCase() : '';
    const contentType = ALLOWED_TYPES[ext];
    if (!contentType) {
      const allowed = Object.keys(ALLOWED_TYPES).sort().join(', ');
      return fileError(`Unsupported file type '${ext || '(none)'}'. Allowed types: ${allowed}.`);
    }
    if (file.size === 0) return fileError('The uploaded file is empty.');
    if (file.size > env.maxUploadMb * 1024 * 1024) {
      return fileError(`File is too large (max ${env.maxUploadMb} MB).`);
    }

    const created = addTenantDocument(tenant.tenantId, { name: filename, size: file.size }, contentType);
    return HttpResponse.json(created, { status: 201 });
  }),
];
