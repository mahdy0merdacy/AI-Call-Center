import { z } from 'zod';
import { api } from '@/lib/apiClient';
import { toApiError } from '@/lib/errors';

const setupResponseSchema = z
  .object({
    otpauth_uri: z.string().startsWith('otpauth://').optional(),
    otpauth_url: z.string().startsWith('otpauth://').optional(),
    qr_code: z.string().min(1),
    secret: z.string().optional(),
  })
  .passthrough();

export interface TotpSetup {
  otpauthUri: string;
  /** Ready-to-use `data:` URL of the PNG. */
  qrDataUrl: string;
  /** Base32 key for manual entry. */
  secret: string;
}

function secretFromUri(uri: string): string {
  try {
    return new URL(uri).searchParams.get('secret') ?? '';
  } catch {
    return '';
  }
}

/** Starts enrolment: returns the otpauth:// URI and its QR code (base64 PNG). */
export async function startTotpSetup(): Promise<TotpSetup> {
  const { data } = await api.post<unknown>('/auth/mfa/totp/setup/');
  const parsed = setupResponseSchema.parse(data);
  const otpauthUri = parsed.otpauth_uri ?? parsed.otpauth_url ?? '';
  const qrDataUrl = parsed.qr_code.startsWith('data:') ? parsed.qr_code : `data:image/png;base64,${parsed.qr_code}`;
  return { otpauthUri, qrDataUrl, secret: parsed.secret ?? secretFromUri(otpauthUri) };
}

const confirmResponseSchema = z
  .object({
    backup_codes: z.array(z.string()).min(1),
    access: z.string().optional(),
  })
  .passthrough();

export interface TotpConfirmResult {
  backupCodes: string[];
  /** Present if the backend reissues tokens once MFA is on. */
  accessToken: string | null;
}

/** Confirms the device with its first code. Backup codes are returned ONCE. */
export async function confirmTotpSetup(code: string): Promise<TotpConfirmResult> {
  const { data } = await api.post<unknown>('/auth/mfa/totp/confirm/', { code });
  const parsed = confirmResponseSchema.parse(data);
  return { backupCodes: parsed.backup_codes, accessToken: parsed.access ?? null };
}

export type TotpConfirmFailure =
  | { kind: 'invalid' }
  | { kind: 'expired' }
  | { kind: 'throttled'; retryAfterSeconds: number | null }
  | { kind: 'other'; message: string };

/** Assumed codes: `invalid_code` / 400 for a wrong code, `setup_expired` / `setup_not_started` to restart. */
export function classifyTotpConfirmError(error: unknown): TotpConfirmFailure {
  const apiError = toApiError(error);
  if (apiError.kind === 'rate_limited') return { kind: 'throttled', retryAfterSeconds: apiError.retryAfterSeconds };
  if (apiError.hasCode('setup_expired', 'setup_not_started', 'no_pending_device')) return { kind: 'expired' };
  if (apiError.kind === 'validation' || apiError.hasCode('invalid_code', 'invalid_otp')) return { kind: 'invalid' };
  return { kind: 'other', message: apiError.message };
}
