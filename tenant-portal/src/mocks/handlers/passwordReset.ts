import { HttpResponse, http } from 'msw';
import { z } from 'zod';
import { db, encodeUid, findUserByEmail, findUserById, issueEmailToken, persist, revokeUserSessions } from '../db';
import { mockInbox } from '../inbox';
import { resetFailures, throttle } from '../security';
import { apiUrl, latency } from '../utils';

const RESET_TTL_SECONDS = 60 * 60;
const NEUTRAL = { detail: 'If an account exists for this email, a password reset link has been sent.' };

function linkError(code: 'token_expired' | 'token_used' | 'invalid_token') {
  const detail = {
    token_expired: 'This link has expired.',
    token_used: 'This link has already been used.',
    invalid_token: 'Invalid link.',
  }[code];
  return HttpResponse.json({ detail, code }, { status: 400 });
}

export const passwordResetHandlers = [
  http.post(apiUrl('/auth/password-reset/'), async ({ request }) => {
    await latency();
    const wait = throttle('password-reset:ip', 5, 60 * 60);
    if (wait) {
      return HttpResponse.json(
        { detail: `Request was throttled. Expected available in ${wait} seconds.` },
        { status: 429, headers: { 'Retry-After': String(wait) } },
      );
    }
    const body = z.object({ email: z.string() }).safeParse(await request.json().catch(() => null));
    const user = body.success ? findUserByEmail(body.data.email) : undefined;
    // Only password accounts get a link; the response never says either way.
    if (user && user.password !== null) {
      mockInbox.send(user.email, 'Reset your password', issueEmailToken('reset', user.id, RESET_TTL_SECONDS));
    }
    return HttpResponse.json(NEUTRAL);
  }),

  http.post(apiUrl('/auth/password-reset/confirm/'), async ({ request }) => {
    await latency();
    const body = z
      .object({ uid: z.string(), token: z.string(), new_password: z.string() })
      .safeParse(await request.json().catch(() => null));
    if (!body.success) return linkError('invalid_token');
    const { uid, token, new_password: password } = body.data;

    // Demo triggers: ?token=expired / ?token=used.
    if (token === 'expired') return linkError('token_expired');
    if (token === 'used') return linkError('token_used');

    const entry = db.emailTokens[token];
    const user = entry ? findUserById(entry.userId) : undefined;
    if (!entry || entry.purpose !== 'reset' || !user || encodeUid(user.id) !== uid) return linkError('invalid_token');
    if (entry.used) return linkError('token_used');
    if (entry.expiresAt <= Date.now()) return linkError('token_expired');

    // Django password validators.
    if (password.length < 10) {
      return HttpResponse.json(
        { new_password: ['This password is too short. It must contain at least 10 characters.'] },
        { status: 400 },
      );
    }
    if (/password|azerty|qwerty|123456/i.test(password)) {
      return HttpResponse.json({ new_password: ['This password is too common.'] }, { status: 400 });
    }
    if (password === user.password) {
      return HttpResponse.json(
        { new_password: ['Your new password must be different from the current one.'] },
        { status: 400 },
      );
    }

    entry.used = true;
    user.password = password;
    persist();
    // Every refresh token of this user is revoked, on every device.
    revokeUserSessions(user.id);
    resetFailures(`account:${user.email}`);
    return HttpResponse.json({ detail: 'Password has been reset.' });
  }),
];
