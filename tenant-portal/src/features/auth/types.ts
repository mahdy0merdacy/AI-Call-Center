import { z } from 'zod';

export const roleSchema = z.enum(['tenant_admin', 'platform_admin']);
export type Role = z.infer<typeof roleSchema>;

export const authProviderSchema = z.enum(['password', 'google']);
export type AuthProvider = z.infer<typeof authProviderSchema>;

/**
 * GET /api/auth/me/ exactly as the backend responds (MeSerializer of the
 * tenant-portal service): `first_name`/`last_name` instead of `full_name`,
 * `email_verified` instead of `is_email_verified` and a nested `tenant`
 * object instead of `tenant_id`. `currentUserSchema` transforms this into
 * the app-wide `CurrentUser` shape, so consumers never change.
 */
const meResponseSchema = z.object({
  id: z.union([z.string(), z.number()]).transform(String),
  email: z.string().email(),
  first_name: z.string().nullable().default(''),
  last_name: z.string().nullable().default(''),
  role: roleSchema,
  tenant: z
    .object({
      id: z.union([z.string(), z.number()]).transform(String),
      name: z.string(),
      slug: z.string().nullable().optional(),
    })
    .nullable()
    .default(null),
  email_verified: z.boolean().default(false),
  mfa_enabled: z.boolean().default(false),
  auth_provider: authProviderSchema.optional(),
  date_joined: z.string().nullable().default(null),
});
type MeResponse = z.infer<typeof meResponseSchema>;

/** "First Last" trimmed; null when the user has no name parts at all. */
function fullName(first: string, last: string): string | null {
  const name = `${first} ${last}`.trim();
  return name.length > 0 ? name : null;
}

export const currentUserSchema = meResponseSchema.transform((user: MeResponse) => ({
  id: user.id,
  email: user.email,
  full_name: fullName(user.first_name ?? '', user.last_name ?? ''),
  role: user.role,
  is_email_verified: user.email_verified,
  tenant_id: user.tenant?.id ?? null,
  tenant_name: user.tenant?.name ?? null,
  mfa_enabled: user.mfa_enabled,
  auth_provider: user.auth_provider ?? ('password' as const),
  date_joined: user.date_joined,
}));
export type CurrentUser = z.infer<typeof currentUserSchema>;

export const tenantStatusSchema = z.enum(['active', 'suspended', 'pending']);

/** GET /api/tenant/me/ (TenantSerializer). */
export const tenantSchema = z.object({
  id: z.union([z.string(), z.number()]).transform(String),
  name: z.string(),
  slug: z.string().nullable().optional(),
  // The backend answers with `is_active`, not `status`; map it below.
  is_active: z.boolean().optional(),
  status: tenantStatusSchema.or(z.string()).optional(),
  created_at: z.string().nullable().optional(),
});
export type Tenant = z.infer<typeof tenantSchema>;
