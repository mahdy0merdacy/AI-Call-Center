import { z } from 'zod';

const booleanString = z
  .enum(['true', 'false'])
  .default('false')
  .transform((value) => value === 'true');

const envSchema = z.object({
  VITE_API_BASE_URL: z.string().default(''),
  // The documents service is a separate backend from the auth service.
  VITE_DOCUMENTS_API_URL: z.string().default(''),
  // Must match the documents service's MAX_UPLOAD_SIZE_MB (the server enforces it anyway).
  VITE_MAX_UPLOAD_MB: z.coerce.number().int().positive().default(25),
  VITE_GOOGLE_AUTH_URL: z.string().default('/_allauth/browser/v1/auth/provider/redirect'),
  // 'post' = allauth headless provider redirect (form POST), 'get' = classic /accounts/google/login/.
  VITE_GOOGLE_AUTH_METHOD: z.enum(['post', 'get']).default('post'),
  VITE_APP_NAME: z.string().min(1).default('AI Call Center'),
  // Optional links; the footer hides any that are unset (no hard-coded hosts).
  VITE_SUPPORT_EMAIL: z.string().email().optional().or(z.literal('').transform(() => undefined)),
  VITE_PRIVACY_URL: z.string().url().optional().or(z.literal('').transform(() => undefined)),
  VITE_TERMS_URL: z.string().url().optional().or(z.literal('').transform(() => undefined)),
  VITE_ENABLE_MSW: booleanString,
  VITE_MOCK_ACCESS_TTL_SECONDS: z.coerce.number().int().positive().default(900),
});

const parsed = envSchema.safeParse(import.meta.env);

if (!parsed.success) {
  throw new Error(`Invalid environment configuration: ${parsed.error.message}`);
}

const apiBaseUrl = parsed.data.VITE_API_BASE_URL.replace(/\/+$/, '');
const documentsBaseUrl = parsed.data.VITE_DOCUMENTS_API_URL.replace(/\/+$/, '');

export const env = {
  /** Backend origin without trailing slash ('' = same origin). */
  apiBaseUrl,
  /** Root every endpoint is resolved against, e.g. `http://localhost:8000/api`. */
  apiRoot: `${apiBaseUrl}/api`,
  /** Root of the documents service, e.g. `http://localhost:8001/api`. */
  documentsRoot: `${documentsBaseUrl}/api`,
  maxUploadMb: parsed.data.VITE_MAX_UPLOAD_MB,
  googleAuthUrl: parsed.data.VITE_GOOGLE_AUTH_URL,
  googleAuthMethod: parsed.data.VITE_GOOGLE_AUTH_METHOD,
  appName: parsed.data.VITE_APP_NAME,
  supportEmail: parsed.data.VITE_SUPPORT_EMAIL,
  privacyUrl: parsed.data.VITE_PRIVACY_URL,
  termsUrl: parsed.data.VITE_TERMS_URL,
  enableMocks: parsed.data.VITE_ENABLE_MSW,
  mockAccessTtlSeconds: parsed.data.VITE_MOCK_ACCESS_TTL_SECONDS,
  isDev: import.meta.env.DEV,
} as const;
