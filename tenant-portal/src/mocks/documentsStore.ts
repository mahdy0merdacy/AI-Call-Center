/**
 * Mock state of the documents service, kept apart from the auth mock state.
 * Rows are keyed by tenant id, like the real service scopes everything by the
 * token's tenant. Persisted to sessionStorage so a reload behaves like a server.
 */
import type { IngestionStatus } from '@/features/documents/types';

export interface MockDocument {
  id: string;
  tenant_id: string;
  original_filename: string;
  content_type: string;
  file_size: number;
  ingestion_status: IngestionStatus;
  error_message: string;
  created_at: string;
  updated_at: string;
}

type DocumentsState = Record<string, MockDocument[]>;

// Bump when the seed shape changes so stale sessionStorage state is ignored.
const STORAGE_KEY = 'msw:documents:v1';

const ago = (minutes: number): string => new Date(Date.now() - minutes * 60_000).toISOString();

function doc(
  tenantId: string,
  filename: string,
  contentType: string,
  size: number,
  status: IngestionStatus,
  minutesAgo: number,
  errorMessage = '',
): MockDocument {
  return {
    id: crypto.randomUUID(),
    tenant_id: tenantId,
    original_filename: filename,
    content_type: contentType,
    file_size: size,
    ingestion_status: status,
    error_message: errorMessage,
    created_at: ago(minutesAgo),
    updated_at: ago(minutesAgo),
  };
}

const PDF = 'application/pdf';
const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

/** Tenant ids match the auth mock seed. Maison Verte starts empty to show the empty state. */
function seed(): DocumentsState {
  return {
    'acme-delivery': [
      doc('acme-delivery', 'delivery-faq.pdf', PDF, 482_113, 'completed', 60 * 26),
      doc('acme-delivery', 'refund-policy.docx', DOCX, 96_240, 'processing', 12),
      doc('acme-delivery', 'shipping-rates.xlsx', XLSX, 31_870, 'failed', 60 * 5, 'The spreadsheet has no readable text.'),
    ],
    'swiftnet-isp': [doc('swiftnet-isp', 'swiftnet-faq.pdf', PDF, 215_004, 'completed', 60 * 48)],
    'maison-verte': [],
  };
}

function load(): DocumentsState {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as DocumentsState;
  } catch {
    // Fall through to a fresh seed.
  }
  return seed();
}

const state: DocumentsState = load();

function persist(): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Ignore quota/private-mode errors in the mock.
  }
}

/** The tenant's documents, newest first (the real service orders by -created_at). */
export function listTenantDocuments(tenantId: string): MockDocument[] {
  return [...(state[tenantId] ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function resetDocumentsStore(): void {
  for (const key of Object.keys(state)) delete state[key];
  Object.assign(state, seed());
  persist();
}
