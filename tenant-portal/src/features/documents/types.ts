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

/**
 * A shareable link a tenant gives its customers. The token is the link's only
 * secret: anyone holding it can start a session, so it is only ever shown to
 * the tenant's own admins.
 */
export const sessionLinkSchema = z.object({
  id: z.string(),
  token: z.string(),
  /** Full shareable address, computed by the service. */
  url: z.string(),
  is_active: z.boolean(),
  /** ISO datetime, or null for a link that never expires. */
  expires_at: z.string().nullable(),
  created_at: z.string(),
});
export type SessionLink = z.infer<typeof sessionLinkSchema>;

export const sessionLinkPageSchema = z.object({
  count: z.number(),
  next: z.string().nullable(),
  previous: z.string().nullable(),
  results: z.array(sessionLinkSchema),
});
export type SessionLinkPage = z.infer<typeof sessionLinkPageSchema>;

/** What the portal shows: `expired` is derived from `expires_at`, it is not stored. */
export type LinkState = 'active' | 'expired' | 'inactive';

export function linkState(link: SessionLink, now: number = Date.now()): LinkState {
  if (!link.is_active) return 'inactive';
  if (link.expires_at !== null && new Date(link.expires_at).getTime() <= now) return 'expired';
  return 'active';
}
