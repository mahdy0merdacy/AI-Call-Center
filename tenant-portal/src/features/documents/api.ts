/**
 * Calls to the documents service (see services/documents/README.md).
 * The tenant always comes from the access token, never from the request.
 *
 *   GET  /api/documents/   -> { count, next, previous, results: Document[] }
 *   POST /api/documents/   multipart/form-data { file } -> Document (201)
 */
import { documentsApi } from '@/lib/apiClient';
import { documentPageSchema, documentSchema, type DocumentPage, type TenantDocument } from './types';

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
