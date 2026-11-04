import { z } from 'zod';

/** Mirrors the documents service's `Document.IngestionStatus`. */
export const ingestionStatusSchema = z.enum(['pending', 'processing', 'completed', 'failed']);
export type IngestionStatus = z.infer<typeof ingestionStatusSchema>;

/** One item of GET /api/documents/. The storage path is never exposed by the API. */
export const documentSchema = z.object({
  id: z.string(),
  original_filename: z.string(),
  content_type: z.string(),
  /** Size in bytes. */
  file_size: z.number(),
  ingestion_status: ingestionStatusSchema,
  /** Filled when `ingestion_status` is `failed`. */
  error_message: z.string().default(''),
  created_at: z.string(),
  updated_at: z.string(),
});
export type TenantDocument = z.infer<typeof documentSchema>;

/** DRF LimitOffsetPagination envelope (default 50 per page, max 200). */
export const documentPageSchema = z.object({
  count: z.number(),
  next: z.string().nullable(),
  previous: z.string().nullable(),
  results: z.array(documentSchema),
});
export type DocumentPage = z.infer<typeof documentPageSchema>;
