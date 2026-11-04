import { HttpResponse, delay } from 'msw';
import { env } from '@/lib/env';
import { findTenant, findUserById, type MockUser } from './db';
import { readAccessToken } from './jwt';

/** Absolute or relative URL of an API path, matching what axios calls. */
export const apiUrl = (path: string): string => `${env.apiRoot}${path}`;

/** Realistic latency so loading states are visible in the demo. */
export const latency = (): Promise<void> => delay(350 + Math.round(Math.random() * 350));

export function drfError(status: number, detail: string, code?: string) {
  return HttpResponse.json(code ? { detail, code } : { detail }, { status });
}

/** Mirrors the backend's CSRF check for cookie-based endpoints. */
export function hasCsrfHeader(request: Request): boolean {
  return request.headers.get('X-Requested-With') === 'XMLHttpRequest';
}

export function serializeUser(user: MockUser) {
  const { password, ...rest } = user;
  return {
    ...rest,
    tenant_name: findTenant(user.tenant_id)?.name ?? null,
    auth_provider: password === null ? 'google' : rest.auth_provider,
  };
}

type AuthResult = { user: MockUser } | { response: Response };

/** Resolves the bearer token like DRF's JWTAuthentication + tenant-active check. */
export function authenticate(request: Request): AuthResult {
  const header = request.headers.get('Authorization');
  const token = header?.startsWith('Bearer ') ? header.slice(7) : null;
  const claims = token ? readAccessToken(token) : null;
  const user = claims ? findUserById(claims.sub) : undefined;

  if (!token) {
    return {
      response: drfError(401, 'Authentication credentials were not provided.', 'not_authenticated'),
    };
  }
  if (!claims || !user) {
    return {
      response: drfError(401, 'Given token not valid for any token type', 'token_not_valid'),
    };
  }
  if (findTenant(user.tenant_id)?.status === 'suspended') {
    return {
      response: drfError(403, 'Your organization has been suspended.', 'tenant_suspended'),
    };
  }
  return { user };
}
