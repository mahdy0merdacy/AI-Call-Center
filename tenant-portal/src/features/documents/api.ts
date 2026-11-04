/**
 * Calls to the documents service (see services/documents/README.md).
 * The tenant always comes from the access token, never from the request.
 *
 *   GET /api/documents/   -> { count, next, previous, results: Document[] }
 */
import { documentsApi } from '@/lib/apiClient';
import { documentPageSchema, type DocumentPage } from './types';

export const PAGE_SIZE = 50;

export async function listDocuments(): Promise<DocumentPage> {
  const { data } = await documentsApi.get<unknown>('/documents/', { params: { limit: PAGE_SIZE } });
  return documentPageSchema.parse(data);
}
