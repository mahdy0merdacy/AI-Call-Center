import { z } from 'zod';

export const roleSchema = z.enum(['tenant_admin', 'platform_admin']);
export type Role = z.infer<typeof roleSchema>;

export const authProviderSchema = z.enum(['password', 'google']);
export type AuthProvider = z.infer<typeof authProviderSchema>;

/**
 * GET /api/auth/me/. Field names follow the backend contract; optional
 * fields default so a slightly older backend still renders.
 */
export const currentUserSchema = z.object({
  id: z.union([z.string(), z.number()]).transform(String),
  email: z.string().email(),
  full_name: z.string().nullable().default(null),
  role: roleSchema,
  is_email_verified: z.boolean().default(false),
  tenant_id: z
    .union([z.string(), z.number()])
    .nullable()
    .default(null)
    .transform((value) => (value === null ? null : String(value))),
  tenant_name: z.string().nullable().optional(),
  mfa_enabled: z.boolean().default(false),
  auth_provider: authProviderSchema.default('password'),
  date_joined: z.string().nullable().default(null),
});
export type CurrentUser = z.infer<typeof currentUserSchema>;

export const tenantStatusSchema = z.enum(['active', 'suspended', 'pending']);

/** GET /api/tenant/me/. */
export const tenantSchema = z.object({
  id: z.union([z.string(), z.number()]).transform(String),
  name: z.string(),
  slug: z.string().nullable().optional(),
  status: tenantStatusSchema.or(z.string()).optional(),
  created_at: z.string().nullable().optional(),
});
export type Tenant = z.infer<typeof tenantSchema>;
