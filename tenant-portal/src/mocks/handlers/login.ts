import { HttpResponse, http } from 'msw';
import { z } from 'zod';
import { DEMO_TOTP, db, findTenant, findUserByEmail, findUserById, persist, randomId, startSession } from '../db';
import { mintAccessToken } from '../jwt';
import { lockRemaining, recordFailure, resetFailures, throttle } from '../security';
import { apiUrl, latency } from '../utils';

/* Demo triggers so every lockout screen can be shown without waiting. */
const DEMO_IP_BLOCKED_EMAIL = 'ipblock@demo.test';
const DEMO_THROTTLED_EMAIL = 'throttle@demo.test';
/** MFA code that simulates a challenge that already expired. */
const DEMO_EXPIRED_CODE = '000000';
/** MFA code that simulates a TOTP already used (replay). */
const DEMO_REPLAY_CODE = '111111';

const ACCOUNT_LOCK = { threshold: 5, seconds: 15 * 60 };
const IP_EMAIL_BLOCK = { threshold: 10, seconds: 60 * 60 };
const MFA_CHALLENGE_SECONDS = 5 * 60;
const MFA_MAX_ATTEMPTS = 5;

const loginBody = z.object({ email: z.string(), password: z.string() });
const mfaBody = z.object({ mfa_token: z.string(), code: z.string() });

interface Challenge {
  userId: string;
  expiresAt: number;
  attempts: number;
}
const challenges = new Map<string, Challenge>();

function json(body: Record<string, unknown>, status: number, retryAfter?: number) {
  return HttpResponse.json(body, {
    status,
    headers: retryAfter ? { 'Retry-After': String(retryAfter) } : undefined,
  });
}

/** Same body for every credential failure, whatever the reason (anti-enumeration). */
const invalidCredentials = () =>
  json({ detail: 'No active account found with the given credentials', code: 'invalid_credentials' }, 401);

function issueTokens(userId: string) {
  const user = findUserById(userId);
  if (!user) return invalidCredentials();
  startSession(user.id);
  return HttpResponse.json({ access: mintAccessToken(user) });
}

export const loginHandlers = [
  http.post(apiUrl('/auth/login/'), async ({ request }) => {
    await latency();
    const parsed = loginBody.safeParse(await request.json().catch(() => null));
    if (!parsed.success) {
      return json({ email: ['This field is required.'], password: ['This field is required.'] }, 400);
    }
    const email = parsed.data.email.trim().toLowerCase();

    const throttled = throttle('login:ip', 10, 60);
    if (throttled || email === DEMO_THROTTLED_EMAIL) {
      const wait = throttled || 42;
      return json({ detail: `Request was throttled. Expected available in ${wait} seconds.` }, 429, wait);
    }

    const ipKey = `ip+email:${email}`;
    const accountKey = `account:${email}`;
    if (email === DEMO_IP_BLOCKED_EMAIL) {
      return json({ detail: 'Too many failed attempts.', code: 'ip_blocked' }, 403, IP_EMAIL_BLOCK.seconds);
    }
    const ipBlocked = lockRemaining(ipKey);
    if (ipBlocked) return json({ detail: 'Too many failed attempts.', code: 'ip_blocked' }, 403, ipBlocked);
    const accountLocked = lockRemaining(accountKey);
    if (accountLocked) {
      return json({ detail: 'Account temporarily locked.', code: 'account_locked' }, 403, accountLocked);
    }

    const user = findUserByEmail(email);
    const valid =
      user !== undefined &&
      user.password !== null &&
      user.password === parsed.data.password &&
      user.is_email_verified &&
      findTenant(user.tenant_id)?.status !== 'suspended';

    if (!valid || !user) {
      // Unknown emails are counted too, so lockout can't reveal which accounts exist.
      const ipNowBlocked = recordFailure(ipKey, IP_EMAIL_BLOCK.threshold, IP_EMAIL_BLOCK.seconds);
      const nowLocked = recordFailure(accountKey, ACCOUNT_LOCK.threshold, ACCOUNT_LOCK.seconds);
      if (ipNowBlocked) {
        return json({ detail: 'Too many failed attempts.', code: 'ip_blocked' }, 403, lockRemaining(ipKey));
      }
      if (nowLocked) {
        return json({ detail: 'Account temporarily locked.', code: 'account_locked' }, 403, lockRemaining(accountKey));
      }
      return invalidCredentials();
    }

    resetFailures(accountKey);
    resetFailures(ipKey);

    if (user.role === 'platform_admin' && user.mfa_enabled) {
      const mfaToken = randomId('mfa');
      challenges.set(mfaToken, {
        userId: user.id,
        expiresAt: Date.now() + MFA_CHALLENGE_SECONDS * 1000,
        attempts: 0,
      });
      return HttpResponse.json({ mfa_required: true, mfa_token: mfaToken, expires_in: MFA_CHALLENGE_SECONDS });
    }

    return issueTokens(user.id);
  }),

  http.post(apiUrl('/auth/mfa/verify/'), async ({ request }) => {
    await latency();
    const parsed = mfaBody.safeParse(await request.json().catch(() => null));
    if (!parsed.success) return json({ code: ['This field is required.'] }, 400);

    const throttled = throttle('mfa:ip', 10, 60);
    if (throttled) {
      return json({ detail: `Request was throttled. Expected available in ${throttled} seconds.` }, 429, throttled);
    }

    const { mfa_token: token } = parsed.data;
    const code = parsed.data.code.replace(/[\s-]/g, '').toUpperCase();
    const challenge = challenges.get(token);

    if (!challenge || challenge.expiresAt <= Date.now() || code === DEMO_EXPIRED_CODE) {
      challenges.delete(token);
      return json({ detail: 'MFA challenge expired. Please log in again.', code: 'mfa_challenge_expired' }, 401);
    }
    if (code === DEMO_REPLAY_CODE) {
      return json({ detail: 'This code has already been used.', code: 'code_already_used' }, 400);
    }

    const backupCodes = db.backupCodes[challenge.userId] ?? [];
    const backupIndex = backupCodes.indexOf(code);
    if (code === DEMO_TOTP || backupIndex >= 0) {
      if (backupIndex >= 0) {
        backupCodes.splice(backupIndex, 1); // single use
        persist();
      }
      challenges.delete(token);
      return issueTokens(challenge.userId);
    }

    challenge.attempts += 1;
    if (challenge.attempts >= MFA_MAX_ATTEMPTS) {
      challenges.delete(token);
      return json({ detail: 'Too many invalid codes. Please log in again.', code: 'mfa_attempts_exceeded' }, 403);
    }
    return json(
      { detail: 'Invalid code.', code: 'invalid_code', attempts_remaining: MFA_MAX_ATTEMPTS - challenge.attempts },
      400,
    );
  }),
];
