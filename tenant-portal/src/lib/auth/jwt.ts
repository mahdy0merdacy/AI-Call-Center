import { z } from 'zod';

const claimsSchema = z.object({ exp: z.number().optional() }).passthrough();

/**
 * Reads the `exp` claim (seconds since epoch) without verifying the signature.
 * Only used to schedule a proactive refresh; the server remains the authority.
 */
export function readTokenExpiry(token: string): number | null {
  const payload = token.split('.')[1];
  if (!payload) return null;
  try {
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
    const json: unknown = JSON.parse(atob(padded));
    const parsed = claimsSchema.safeParse(json);
    return parsed.success ? (parsed.data.exp ?? null) : null;
  } catch {
    return null;
  }
}
