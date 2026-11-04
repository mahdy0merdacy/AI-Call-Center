import { HttpResponse, http } from 'msw';
import { z } from 'zod';
import { env } from '@/lib/env';
import { DEMO_TOTP, db, persist } from '../db';
import { throttle } from '../security';
import { apiUrl, authenticate, latency } from '../utils';

const SETUP_TTL_MS = 10 * 60 * 1000;
const BASE32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
const CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

/** user id -> pending (unconfirmed) TOTP device. */
const pending = new Map<string, { secret: string; createdAt: number }>();

function randomFrom(alphabet: string, length: number): string {
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join('');
}

/**
 * Decorative QR-like PNG (finder squares + modules seeded by the secret). The
 * real backend returns a scannable QR; the mock accepts the code 123456 anyway.
 */
function fakeQrBase64(seed: string): string {
  const size = 29;
  const scale = 8;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = (size + 4) * scale;
  const context = canvas.getContext('2d');
  if (!context) return '';
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = '#0b1020';

  let state = Array.from(seed).reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 7);
  const next = () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
  const inFinder = (x: number, y: number) =>
    (x < 8 && y < 8) || (x >= size - 8 && y < 8) || (x < 8 && y >= size - 8);

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      if (!inFinder(x, y) && next() > 0.52) context.fillRect((x + 2) * scale, (y + 2) * scale, scale, scale);
    }
  }
  for (const [fx, fy] of [[0, 0], [size - 7, 0], [0, size - 7]] as const) {
    context.fillRect((fx + 2) * scale, (fy + 2) * scale, 7 * scale, 7 * scale);
    context.fillStyle = '#ffffff';
    context.fillRect((fx + 3) * scale, (fy + 3) * scale, 5 * scale, 5 * scale);
    context.fillStyle = '#0b1020';
    context.fillRect((fx + 4) * scale, (fy + 4) * scale, 3 * scale, 3 * scale);
  }
  // The real API sends bare base64 (no data: prefix).
  return canvas.toDataURL('image/png').replace(/^data:image\/png;base64,/, '');
}

export const mfaSetupHandlers = [
  http.post(apiUrl('/auth/mfa/totp/setup/'), async ({ request }) => {
    await latency();
    const result = authenticate(request);
    if ('response' in result) return result.response;
    const { user } = result;
    if (user.mfa_enabled) {
      return HttpResponse.json({ detail: 'MFA is already enabled.', code: 'mfa_already_enabled' }, { status: 400 });
    }
    const secret = randomFrom(BASE32, 32);
    pending.set(user.id, { secret, createdAt: Date.now() });
    const issuer = encodeURIComponent(env.appName);
    const otpauthUri = `otpauth://totp/${issuer}:${encodeURIComponent(user.email)}?secret=${secret}&issuer=${issuer}&digits=6&period=30`;
    return HttpResponse.json({ otpauth_uri: otpauthUri, qr_code: fakeQrBase64(secret) });
  }),

  http.post(apiUrl('/auth/mfa/totp/confirm/'), async ({ request }) => {
    await latency();
    const result = authenticate(request);
    if ('response' in result) return result.response;
    const { user } = result;

    const wait = throttle(`mfa-confirm:${user.id}`, 10, 60);
    if (wait) {
      return HttpResponse.json(
        { detail: `Request was throttled. Expected available in ${wait} seconds.` },
        { status: 429, headers: { 'Retry-After': String(wait) } },
      );
    }

    const device = pending.get(user.id);
    if (!device || Date.now() - device.createdAt > SETUP_TTL_MS) {
      pending.delete(user.id);
      return HttpResponse.json({ detail: 'No setup in progress.', code: 'setup_expired' }, { status: 400 });
    }
    const body = z.object({ code: z.string() }).safeParse(await request.json().catch(() => null));
    if (!body.success || body.data.code !== DEMO_TOTP) {
      return HttpResponse.json({ detail: 'Invalid code.', code: 'invalid_code' }, { status: 400 });
    }

    pending.delete(user.id);
    const codes = Array.from({ length: 10 }, () => randomFrom(CODE_ALPHABET, 8));
    db.backupCodes[user.id] = codes;
    user.mfa_enabled = true;
    persist();
    return HttpResponse.json({ backup_codes: codes.map((code) => `${code.slice(0, 4)}-${code.slice(4)}`) });
  }),
];
