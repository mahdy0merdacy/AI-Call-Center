import { HttpResponse, http } from 'msw';
import { z } from 'zod';
import { slugify } from '@/lib/slug';
import { db, encodeUid, findUserByEmail, findUserById, issueEmailToken, persist, randomId, type MockUser } from '../db';
import { mockInbox } from '../inbox';
import { throttle } from '../security';
import { apiUrl, latency } from '../utils';

const VERIFY_TTL_SECONDS = 24 * 60 * 60;
const NEUTRAL = { detail: 'If this email can be used, a verification email has been sent.' };

const registerBody = z.object({
  company_name: z.string().optional(),
  email: z.string().optional(),
  password: z.string().optional(),
});

function throttled(seconds: number) {
  return HttpResponse.json(
    { detail: `Request was throttled. Expected available in ${seconds} seconds.` },
    { status: 429, headers: { 'Retry-After': String(seconds) } },
  );
}

function sendVerification(user: MockUser) {
  const link = issueEmailToken('verify', user.id, VERIFY_TTL_SECONDS);
  mockInbox.send(user.email, 'Verify your email address', link);
}

export const registrationHandlers = [
  http.post(apiUrl('/auth/register/'), async ({ request }) => {
    await latency();
    const wait = throttle('register:ip', 5, 60 * 60); // 5 per hour
    if (wait) return throttled(wait);

    const body = registerBody.parse(await request.json().catch(() => ({})));
    const companyName = body.company_name?.trim() ?? '';
    const email = body.email?.trim().toLowerCase() ?? '';
    const password = body.password ?? '';

    // Mimics DRF serializer + Django password validators.
    const errors: Record<string, string[]> = {};
    if (!companyName) errors.company_name = ['This field is required.'];
    if (!z.string().email().safeParse(email).success) errors.email = ['Enter a valid email address.'];
    if (password.length < 10) errors.password = ['This password is too short. It must contain at least 10 characters.'];
    else if (/password|azerty|qwerty|123456/i.test(password)) errors.password = ['This password is too common.'];
    if (Object.keys(errors).length > 0) return HttpResponse.json(errors, { status: 400 });

    // Existing email: same response, nothing sent, no hint (anti-enumeration).
    if (findUserByEmail(email)) return HttpResponse.json(NEUTRAL, { status: 201 });

    const tenantId = randomId('t');
    db.tenants.push({
      id: tenantId,
      name: companyName,
      slug: slugify(companyName) || tenantId,
      status: 'active',
      created_at: new Date().toISOString(),
    });
    const user: MockUser = {
      id: randomId('u'),
      email,
      password,
      full_name: null,
      role: 'tenant_admin',
      is_email_verified: false,
      tenant_id: tenantId,
      mfa_enabled: false,
      auth_provider: 'password',
      date_joined: new Date().toISOString(),
    };
    db.users.push(user);
    persist();
    sendVerification(user);
    return HttpResponse.json(NEUTRAL, { status: 201 });
  }),

  http.post(apiUrl('/auth/resend-verification/'), async ({ request }) => {
    await latency();
    const wait = throttle('resend:ip', 5, 60 * 60);
    if (wait) return throttled(wait);
    const body = z.object({ email: z.string() }).safeParse(await request.json().catch(() => null));
    const user = body.success ? findUserByEmail(body.data.email) : undefined;
    if (user && !user.is_email_verified && user.password !== null) sendVerification(user);
    return HttpResponse.json(NEUTRAL);
  }),

  http.post(apiUrl('/auth/verify-email/'), async ({ request }) => {
    await latency();
    const body = z.object({ uid: z.string(), token: z.string() }).safeParse(await request.json().catch(() => null));
    if (!body.success) return HttpResponse.json({ detail: 'Invalid link.', code: 'invalid_token' }, { status: 400 });

    // Demo triggers: ?token=expired / ?token=used.
    if (body.data.token === 'expired') {
      return HttpResponse.json({ detail: 'This link has expired.', code: 'token_expired' }, { status: 400 });
    }
    if (body.data.token === 'used') {
      return HttpResponse.json({ detail: 'This link has already been used.', code: 'token_used' }, { status: 400 });
    }

    const entry = db.emailTokens[body.data.token];
    const user = entry ? findUserById(entry.userId) : undefined;
    if (!entry || entry.purpose !== 'verify' || !user || encodeUid(user.id) !== body.data.uid) {
      return HttpResponse.json({ detail: 'Invalid link.', code: 'invalid_token' }, { status: 400 });
    }
    if (entry.used) {
      return HttpResponse.json({ detail: 'This link has already been used.', code: 'token_used' }, { status: 400 });
    }
    if (entry.expiresAt <= Date.now()) {
      return HttpResponse.json({ detail: 'This link has expired.', code: 'token_expired' }, { status: 400 });
    }
    entry.used = true;
    user.is_email_verified = true;
    persist();
    return HttpResponse.json({ detail: 'Email verified.' });
  }),
];
