/**
 * Platform-admin tenant management.
 *
 *   GET   /api/admin/tenants/                      -> all tenants (paginated)
 *   POST  /api/admin/tenants/<id>/suspend/         -> { tenant }
 *   POST  /api/admin/tenants/<id>/unsuspend/       -> { tenant }
 */
import { z } from 'zod';
import { api } from '@/lib/apiClient';

const tenantRowSchema = z.object({
  id: z.union([z.string(), z.number()]).transform(String),
  name: z.string(),
  slug: z.string(),
  is_active: z.boolean(),
  created_at: z.string().nullable().optional(),
});

export interface TenantAdmin {
  id: string;
  name: string;
  slug: string;
  isActive: boolean;
  createdAt: string | null;
}

/** Same list, but with an app-friendly shape. */
function toRow(raw: z.infer<typeof tenantRowSchema>): TenantAdmin {
  return { id: raw.id, name: raw.name, slug: raw.slug, isActive: raw.is_active, createdAt: raw.created_at ?? null };
}

const listSchema = z.union([
  z.array(tenantRowSchema),
  z.object({ results: z.array(tenantRowSchema) }).passthrough().transform((page) => page.results),
]);

function tenantFrom(responseData: unknown): TenantAdmin {
  return toRow(tenantRowSchema.parse(responseData));
}

export async function listTenants(): Promise<TenantAdmin[]> {
  const { data } = await api.get<unknown>('/admin/tenants/');
  return listSchema.parse(data).map(toRow);
}

export async function suspendTenant(id: string): Promise<TenantAdmin> {
  const { data } = await api.post<unknown>(`/admin/tenants/${id}/suspend/`);
  return tenantFrom(data);
}

export async function unsuspendTenant(id: string): Promise<TenantAdmin> {
  const { data } = await api.post<unknown>(`/admin/tenants/${id}/unsuspend/`);
  return tenantFrom(data);
}
