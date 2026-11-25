/**
 * Calls to the documents service (see services/documents/README.md).
 * The tenant always comes from the access token, never from the request.
 *
 *   GET  /api/documents/   -> { count, next, previous, results: Document[] }
 *   POST /api/documents/   multipart/form-data { file } -> Document (201)
 *
 *   GET  /api/session-links/                   -> { count, next, previous, results: SessionLink[] }
 *   POST /api/session-links/                   { expires_at? } -> SessionLink (201)
 *   POST /api/session-links/<id>/deactivate/   -> SessionLink (idempotent)
 */
import { documentsApi } from '@/lib/apiClient';
import {
  documentPageSchema,
  documentSchema,
  sessionLinkPageSchema,
  sessionLinkSchema,
  type DocumentPage,
  type SessionLink,
  type SessionLinkPage,
  type TenantDocument,
} from './types';

export const PAGE_SIZE = 50;

/** Uploads can be large and slow; the default 20 s request timeout is too short. */
const UPLOAD_TIMEOUT_MS = 5 * 60_000;

export async function listDocuments(): Promise<DocumentPage> {
  const { data } = await documentsApi.get<unknown>('/documents/', { params: { limit: PAGE_SIZE } });
  return documentPageSchema.parse(data);
}

/** `onProgress` receives 0..1 as the file is sent. */
export async function uploadDocument(file: File, onProgress?: (fraction: number) => void): Promise<TenantDocument> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await documentsApi.post<unknown>('/documents/', form, {
    // The browser adds the multipart boundary itself.
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: UPLOAD_TIMEOUT_MS,
    onUploadProgress: (event) => onProgress?.(event.progress ?? 0),
  });
  return documentSchema.parse(data);
}

export async function listSessionLinks(): Promise<SessionLinkPage> {
  const { data } = await documentsApi.get<unknown>('/session-links/', { params: { limit: PAGE_SIZE } });
  return sessionLinkPageSchema.parse(data);
}

/** `expiresAt` is an ISO datetime in the future, or null for a link that never expires. */
export async function createSessionLink(expiresAt: string | null): Promise<SessionLink> {
  const { data } = await documentsApi.post<unknown>('/session-links/', expiresAt ? { expires_at: expiresAt } : {});
  return sessionLinkSchema.parse(data);
}

export async function deactivateSessionLink(id: string): Promise<SessionLink> {
  const { data } = await documentsApi.post<unknown>(`/session-links/${encodeURIComponent(id)}/deactivate/`);
  return sessionLinkSchema.parse(data);
}
