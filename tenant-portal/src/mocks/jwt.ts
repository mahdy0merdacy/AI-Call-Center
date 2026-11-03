/** Unsigned JWT-shaped tokens for the mock backend (the frontend only reads `exp`). */
import { env } from '@/lib/env';
import type { MockUser } from './db';

interface MockClaims {
  sub: string;
  role: MockUser['role'];
  tenant_id: string | null;
  iat: number;
  exp: number;
}

function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** `ttlSeconds` < 0 mints an already-expired token (devtools: force a 401 → refresh). */
export function mintAccessToken(user: MockUser, ttlSeconds: number = env.mockAccessTtlSeconds): string {
  const now = Math.floor(Date.now() / 1000);
  const claims: MockClaims = {
    sub: user.id,
    role: user.role,
    tenant_id: user.tenant_id,
    iat: now,
    exp: now + ttlSeconds,
  };
  const header = base64Url(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  return `${header}.${base64Url(JSON.stringify(claims))}.mock`;
}

export function readAccessToken(token: string): MockClaims | null {
  const payload = token.split('.')[1];
  if (!payload) return null;
  try {
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const claims = JSON.parse(json) as MockClaims;
    return claims.exp * 1000 > Date.now() ? claims : null;
  } catch {
    return null;
  }
}
