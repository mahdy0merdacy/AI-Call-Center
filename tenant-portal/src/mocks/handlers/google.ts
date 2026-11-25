import { HttpResponse, http } from 'msw';
import { z } from 'zod';
import { slugify } from '@/lib/slug';
import { db, findUserByEmail, persist, randomId, startSession, type MockUser } from '../db';
import { mintAccessToken } from '../jwt';
import { apiUrl, authenticate, hasCsrfHeader, latency } from '../utils';

const EXCHANGE_WINDOW_MS = 5 * 60 * 1000;

function error(status: number, code: string, detail: string) {
  return HttpResponse.json({ detail, code }, { status });
}

export const googleHandlers = [
  http.post(apiUrl('/auth/google/exchange/'), async ({ request }) => {
    await latency();
    if (!hasCsrfHeader(request)) return error(403, 'csrf_failed', 'CSRF Failed: missing X-Requested-With header.');

    const session = db.googleSession;
    // The allauth session is single use.
    db.googleSession = null;
    persist();

    if (!session) return error(401, 'no_social_session', 'No Google sign-in in progress.');
    if (Date.now() - session.createdAt > EXCHANGE_WINDOW_MS) {
      return error(401, 'google_session_expired', 'The Google sign-in has expired. Please try again.');
    }
    if (session.scenario === 'unverified') {
      return error(400, 'email_not_verified', 'Google has not verified this email address.');
    }

    // Identify by Google `sub`, never by email.
    let user = db.users.find((candidate) => candidate.google_sub === session.sub);
    if (!user) {
      // A password account with this email: refuse, no automatic linking.
      if (findUserByEmail(session.email)) {
        return error(409, 'email_already_registered', 'This email is already registered with a password.');
      }
      const created: MockUser = {
        id: randomId('u'),
        email: session.email,
        password: null,
        full_name: session.name,
        role: 'tenant_admin',
        is_email_verified: true,
        tenant_id: null,
        mfa_enabled: false,
        auth_provider: 'google',
        date_joined: new Date().toISOString(),
        google_sub: session.sub,
      };
      db.users.push(created);
      persist();
      user = created;
    }

    startSession(user.id);
    return HttpResponse.json({
      access: mintAccessToken(user),
      onboarding_required: user.tenant_id === null,
    });
  }),

  http.post(apiUrl('/auth/onboarding/'), async ({ request }) => {
    await latency();
    const result = authenticate(request);
    if ('response' in result) return result.response;
    const { user } = result;

    if (user.auth_provider !== 'google') {
      return error(403, 'not_allowed', 'Onboarding is only for Google sign-ups.');
    }
    if (user.tenant_id !== null) {
      return error(409, 'tenant_already_exists', 'Your account already has a company.');
    }

    const body = z.object({ company_name: z.string().optional() }).parse(await request.json().catch(() => ({})));
    const name = body.company_name?.trim() ?? '';
    if (!name) return HttpResponse.json({ company_name: ['This field is required.'] }, { status: 400 });
    if (name.length > 100) {
      return HttpResponse.json({ company_name: ['Ensure this field has no more than 100 characters.'] }, { status: 400 });
    }
    const slug = slugify(name);
    if (db.tenants.some((tenant) => tenant.slug === slug)) {
      return HttpResponse.json({ company_name: ['A company with a similar name already exists.'] }, { status: 400 });
    }

    const tenantId = randomId('t');
    db.tenants.push({ id: tenantId, name, slug: slug || tenantId, status: 'active', created_at: new Date().toISOString() });
    user.tenant_id = tenantId;
    persist();
    startSession(user.id); // new tokens carry tenant_id
    return HttpResponse.json({ access: mintAccessToken(user), tenant_id: tenantId }, { status: 201 });
  }),
];
