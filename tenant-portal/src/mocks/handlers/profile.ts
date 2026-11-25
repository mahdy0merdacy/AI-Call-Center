/** Mock of the PROPOSED profile endpoints (see features/profile/api.ts). */
import { HttpResponse, http } from 'msw';
import { z } from 'zod';
import { persist, revokeUserSessions, startSession } from '../db';
import { mintAccessToken } from '../jwt';
import { throttle } from '../security';
import { apiUrl, authenticate, latency, serializeUser } from '../utils';

export const profileHandlers = [
  http.patch(apiUrl('/auth/me/'), async ({ request }) => {
    await latency();
    const result = authenticate(request);
    if ('response' in result) return result.response;
    const body = z.object({ full_name: z.string().optional() }).safeParse(await request.json().catch(() => null));
    if (!body.success) return HttpResponse.json({ full_name: ['Invalid value.'] }, { status: 400 });
    const fullName = body.data.full_name?.trim() ?? '';
    if (fullName.length > 150) {
      return HttpResponse.json({ full_name: ['Ensure this field has no more than 150 characters.'] }, { status: 400 });
    }
    // Demo trigger: a name containing "error" fails, to show the optimistic rollback.
    if (/error/i.test(fullName)) {
      return HttpResponse.json({ detail: 'Simulated server error.' }, { status: 500 });
    }
    result.user.full_name = fullName || null;
    persist();
    return HttpResponse.json(serializeUser(result.user));
  }),

  http.post(apiUrl('/auth/change-password/'), async ({ request }) => {
    await latency();
    const result = authenticate(request);
    if ('response' in result) return result.response;
    const { user } = result;

    const wait = throttle(`change-password:${user.id}`, 5, 60);
    if (wait) {
      return HttpResponse.json(
        { detail: `Request was throttled. Expected available in ${wait} seconds.` },
        { status: 429, headers: { 'Retry-After': String(wait) } },
      );
    }
    if (user.password === null) {
      return HttpResponse.json({ detail: 'This account has no password.', code: 'no_password' }, { status: 400 });
    }

    const body = z
      .object({ current_password: z.string(), new_password: z.string() })
      .safeParse(await request.json().catch(() => null));
    if (!body.success) return HttpResponse.json({ detail: 'Invalid request.' }, { status: 400 });
    const { current_password: current, new_password: next } = body.data;

    if (current !== user.password) {
      return HttpResponse.json({ current_password: ['Your current password is incorrect.'] }, { status: 400 });
    }
    if (next.length < 10) {
      return HttpResponse.json(
        { new_password: ['This password is too short. It must contain at least 10 characters.'] },
        { status: 400 },
      );
    }
    if (/password|azerty|qwerty|123456/i.test(next)) {
      return HttpResponse.json({ new_password: ['This password is too common.'] }, { status: 400 });
    }

    user.password = next;
    persist();
    // Sign out every other device; this browser gets a fresh session.
    revokeUserSessions(user.id);
    startSession(user.id);
    return HttpResponse.json({ detail: 'Password updated.', access: mintAccessToken(user) });
  }),
];
