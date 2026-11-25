# Tenant Portal — Authentication frontend

React 18 + TypeScript + Vite web portal where a company admin (a tenant, such as
Acme Delivery, SwiftNet or Maison Verte) signs up, signs in and manages their
account on the AI Call Center platform.

> **Backend status:** the portal targets a Django REST Framework auth service
> (JWT access token + HttpOnly refresh cookie) that is **not in this repository
> yet**. Until it lands, run the portal against the bundled MSW mock backend
> (`npm run dev:mock`), which implements the same contract.

Once signed in, a company admin also manages the **knowledge documents** their voice
agent uses and the **session links** they share with customers. That page talks to a
separate backend, the documents service (`services/documents`). See
[Documents](#documents).

## Quick start

```bash
npm install
cp .env.example .env        # then edit VITE_API_BASE_URL
npm run dev                 # against the real backend
npm run dev:mock            # against the MSW mock backend (no Django needed)
```

Other scripts: `npm run typecheck`, `npm run build`, `npm run build:mock`, `npm run preview`.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `''` (same origin) | Backend origin. Endpoints resolve under `${VITE_API_BASE_URL}/api/`. |
| `VITE_DOCUMENTS_API_URL` | `''` (same origin) | Documents service origin. Its endpoints resolve under `${VITE_DOCUMENTS_API_URL}/api/`. |
| `VITE_MAX_UPLOAD_MB` | `25` | Largest upload the portal accepts before sending. Keep it equal to the service's `MAX_UPLOAD_SIZE_MB`; the service enforces its own limit anyway. |
| `VITE_GOOGLE_AUTH_URL` | `/_allauth/browser/v1/auth/provider/redirect` | allauth Google entry point used by "Continue with Google". |
| `VITE_GOOGLE_AUTH_METHOD` | `post` | `post`: allauth headless provider redirect (a form POST with `provider`, `callback_url`, `process`). `get`: classic `/accounts/google/login/?process=login&next=…`. |
| `VITE_APP_NAME` | `AI Call Center` | Product name shown in the UI. |
| `VITE_SUPPORT_EMAIL` | unset | "Help" link in the footer; hidden when unset. |
| `VITE_PRIVACY_URL` / `VITE_TERMS_URL` | unset | Legal links in the footer; hidden when unset. |
| `VITE_ENABLE_MSW` | `false` | `true` intercepts all API calls with the mock backend. |
| `VITE_MOCK_ACCESS_TTL_SECONDS` | `900` | Mock only: access-token lifetime. Lower it (e.g. `75`) to watch the silent refresh. |

## Mock backend (MSW)

- `npm run dev:mock` loads `.env.mock`, which sets `VITE_ENABLE_MSW=true`.
  To mock with your own env file, set `VITE_ENABLE_MSW=true` in it.
- Handlers live in `src/mocks/handlers/`. Mock state is kept in
  `sessionStorage` (`msw:state:v5`) so a reload behaves like a real server.
  Reset it with *Mock devtools → Reset mock data*, or `window.__mockDb.reset()`.
- The mock enforces the same rules as the backend: `X-Requested-With`
  on cookie endpoints, refresh-token rotation and blacklisting, and
  `tenant_suspended` blocking.
- Seed tenants mirror [`tenants/*.yaml`](../tenants/README.md) (same `tenant_id`,
  name and slug). Seed accounts, all with the password `Demo!Pass2024`:

  | Account | Role | Tenant |
  | --- | --- | --- |
  | `admin@acme.test` | tenant admin | Acme Delivery |
  | `admin@swiftnet.test` | tenant admin | SwiftNet Broadband |
  | `admin@maisonverte.test` | tenant admin | Maison Verte (fr-FR) |
  | `admin@suspended.test` | tenant admin | Demo Suspended Co (mock only, suspended) |
  | `platform@aicc.test` | platform admin, MFA enrolled | none |
  | `platform-new@aicc.test` | platform admin, no MFA yet (sent to `/mfa/setup`) | none |
  | `unverified@acme.test` | tenant admin, email not verified (login fails generically) | Acme Delivery |

- Scenario triggers:

  | Do this | To see |
  | --- | --- |
  | 5 wrong passwords for one email | account locked for 15 min (countdown) |
  | Sign in as `ipblock@demo.test` | IP + email block for 1 h |
  | Sign in as `throttle@demo.test` (or more than 10 tries in a minute) | HTTP 429 with `Retry-After` |
  | Sign in as `admin@suspended.test` / `unverified@acme.test` | the same generic error as a wrong password |
  | MFA code `123456` | valid TOTP |
  | MFA backup code `K7PX-M2QD` (9 more in `src/mocks/db.ts`) | backup code, single use |
  | MFA code `111111` | replayed code |
  | MFA code `000000` | expired challenge |
  | 5 wrong MFA codes | challenge cancelled, sign in again |
  | Sign up with an email that already exists | the same "check your inbox" screen, and no email is sent |
  | Sign up with a password containing "password" | a backend validation error mapped onto the password field |
  | 6 sign-ups within an hour | HTTP 429 (5 per hour) |
  | `/verify-email?uid=x&token=expired` | expired link |
  | `/verify-email?uid=x&token=used` | link already used |
  | `/verify-email` with no or a wrong token | invalid link |
  | *Forgot password* with any email | the same neutral confirmation; a link reaches the mock inbox only for existing password accounts |
  | Open the reset link while signed in, then set a password | every session is revoked; `/dashboard` then goes to sign-in |
  | Open the same reset link again | link already used |
  | `/reset-password?uid=x&token=expired` / no token | expired / invalid link |
  | *Continue with Google* | a mock account chooser (stands in for Google): new user → onboarding, Léa Martin (existing Google user, Maison Verte) → dashboard, `admin@acme.test` → `email_already_registered`, unverified Google email, a sign-in older than 5 min, or cancelled |
  | Open `/onboarding` once you have a company | sent back to the dashboard |
  | `/profile` → change your name | the header updates before the server answers (optimistic update) |
  | Name containing "error" | simulated 500: the change is rolled back and a toast is shown |
  | Security tab → wrong current password | error on the current-password field |
  | Security tab → change password | other sessions revoked; this browser stays signed in |
  | `/profile?tab=security` as Léa Martin (Google) | "Signed in with Google" card instead of the password form |
  | Sign in as `platform-new@aicc.test` | forced to `/mfa/setup`. The QR code is decorative in mock mode; confirm with `123456`. You get 10 one-time backup codes, then can sign in again with MFA |
  | `/documents` as `admin@acme.test` | 3 seeded documents (completed, processing, failed) and 4 session links (active, active with expiry, deactivated, expired) |
  | `/documents` as `admin@maisonverte.test` | empty states for both lists (each tenant only sees its own data) |
  | `/documents` as `platform@aicc.test` | no *Documents* link; opening the URL goes to `/forbidden` |
  | Upload a `.png`, an empty file or one over 25 MB | refused in the browser with a toast (drag it in: the file picker already hides unsupported types) |
  | Create a session link with a past date | "Choose a date and time in the future." |

- **Mock devtools** (bottom-right button, mock mode only) has two tabs:
  - **Scenarios**:

    | Action | What happens |
    | --- | --- |
    | Expire the access token | `/auth/me/` and `/tenant/me/` fire in parallel and both get a 401. **One** `/refresh/` runs, then both are replayed. |
    | Revoke the refresh session | the refresh fails → forced sign-out with "session expired", keeping the return URL |
    | Suspend my tenant | the next request gets 403 `tenant_suspended` → `/suspended` |
    | Simulate network failure | every API call fails like a dropped connection → banner, and the session is kept |
    | Throw a render error | the error boundary screen |
    | Reset mock data | back to the seed, signed out (documents and session links included) |

  - **Inbox**: emails the mock backend "sent" (verification, password reset), each
    with an *Open link* button. Tokens are never logged to the console.

## Languages

English and French ship together: Maison Verte runs entirely in French. The language
comes from the visitor's browser, or from the EN/FR toggle (stored in
`localStorage`). Copy lives in `src/i18n/en.ts` and `src/i18n/fr.ts`. The
`Messages` type makes the build fail if a French string is missing.

## Documents

`/documents` (company admins only) has three parts:

- **Upload**: click or drag in one or several files. Accepted types are `pdf`, `docx`,
  `xlsx`, `csv` and `txt`, up to `VITE_MAX_UPLOAD_MB`. The browser checks the type, an
  empty file, the size and the name length first (`features/documents/validation.ts`).
  The service repeats those checks and also sniffs the file's content, so it stays the
  source of truth and its message is shown when it refuses a file. Files go up one
  request at a time, with a progress bar.
- **Documents table**: name, type, size, upload date and an ingestion status badge
  (`pending`, `processing`, `completed`, `failed`; a failed document shows its error).
  Only the latest 50 are listed, and the page says so when there are more.
- **Session links**: create a link with an optional expiry, copy it, and deactivate it
  (after a confirmation: the service cannot turn a link back on). *Expired* is derived
  in the browser from `expires_at`; the service only stores `is_active`.

The documents service is a different backend, so `documentsApi` in `src/lib/apiClient.ts`
has its own base URL (`VITE_DOCUMENTS_API_URL`). It shares the auth client's bearer
token, silent refresh and suspension handling (`installAuth`).

| Method | Path | Used for |
| --- | --- | --- |
| `GET` | `/api/documents/?limit=50` | the table (`{count, next, previous, results}`) |
| `POST` | `/api/documents/` | upload (`multipart/form-data`, field `file`) |
| `GET` | `/api/session-links/?limit=50` | the links list |
| `POST` | `/api/session-links/` | create (`{expires_at?}`, ISO datetime in the future) |
| `POST` | `/api/session-links/<id>/deactivate/` | deactivate (idempotent) |

Running against the real service (`npm run dev`) needs three things on the backend side:

1. The auth service must sign access tokens with **RS256**, with `iss` = `tenant-portal`,
   `aud` = `ai-voice-platform`, a `role` claim and a **UUID** `tenant_id` claim. The mock's
   tokens are unsigned and its tenant ids are slugs (`acme-delivery`), so they only work
   against the mock.
2. The documents service's `JWT_PUBLIC_KEY` must be that auth service's public key.
3. Its `CORS_ALLOWED_ORIGINS` must include this portal's origin
   (`http://localhost:5173` in development).

The mock (`src/mocks/handlers/documents.ts`, `sessionLinks.ts`) applies the same rules
as the service, keeps its data per tenant in `sessionStorage`, and does not sniff file
content. Stored mock uploads keep only the name and size.

## CI

`tenant-portal/.gitlab-ci.yml` is a child pipeline that the root
`.gitlab-ci.yml` triggers when `tenant-portal/` changes. It runs `typecheck`,
then builds both the real bundle and the mock bundle.

## Auth architecture

- **Access token**: kept **in memory only** (`src/lib/auth/tokenStore.ts`),
  never in Web Storage. A reload restores the session through
  `POST /api/auth/refresh/` while a branded splash shows.
- **Refresh token**: HttpOnly cookie. Refresh and logout calls send
  `withCredentials: true` and `X-Requested-With: XMLHttpRequest`
  (`cookieAuthConfig` in `src/lib/apiClient.ts`).
- **Single-flight refresh**: every caller shares one in-flight promise, and
  the Web Locks API serialises refreshes across tabs. This matters because
  the backend blacklists the previous refresh token on each rotation.
- **Interceptor**: on a 401, the client refreshes once and replays the request.
  If another request already rotated the token, it replays with the current
  token without refreshing again. If the refresh fails, local state is cleared
  and the user is sent to `/login` with a "session expired" notice. A network
  error or 5xx keeps the session.
- **Proactive refresh**: scheduled 60 s before the access token's `exp`.
- **Suspension**: a 401/403 with code `tenant_suspended` (or
  `account_suspended`) on an authenticated request clears the session and
  routes to `/suspended`.
- **Errors**: every failure rejects with `ApiRequestError` (`src/lib/errors.ts`),
  which carries `kind`, `status`, `code`, `fieldErrors` (DRF `{field: [msgs]}`),
  `formErrors` (`non_field_errors`/`detail`) and `retryAfterSeconds`
  (from the `Retry-After` header or the DRF throttle message).
  `applyServerErrors()` (`src/lib/forms.ts`) maps them onto react-hook-form fields.
- **Cross-tab sign-out**: through `BroadcastChannel('tp-auth')`.

## Backend contract assumptions

These are assumptions where the backend docs are silent. Each is isolated in
one place so it is easy to change:

| Assumption | Where |
| --- | --- |
| Token responses look like `{ "access": "<jwt>" }` (`access_token` is also accepted) | `extractAccessToken` in `lib/apiClient.ts` |
| A suspended tenant returns `code: "tenant_suspended"` | `SUSPENDED_CODES` in `lib/apiClient.ts` |
| Error codes come in `code`, `error_code` or `error` | `readCode` in `lib/errors.ts` |
| `/auth/me/` fields: `id, email, full_name, role, is_email_verified, tenant_id, mfa_enabled, auth_provider, date_joined` | `currentUserSchema` in `features/auth/types.ts` |
| Lockouts are HTTP 429, or carry code `account_locked` / `ip_blocked`; `Retry-After` gives the duration | `classifyLoginError` in `features/auth/api/login.ts` |
| MFA errors: `invalid_code` (+ `attempts_remaining`), `code_already_used`, `mfa_challenge_expired`, `mfa_attempts_exceeded` | `classifyMfaError` in `features/auth/api/login.ts` |
| Register body is `{company_name, email, password}`; terms acceptance is checked in the UI only | `registerRequest` in `features/auth/api/registration.ts` |
| Verify-email errors: `token_expired`, `token_used` / `already_verified`; any other 4xx = invalid link | `classifyVerifyError` in `features/auth/api/registration.ts` |
| Reset confirm body is `{uid, token, new_password}`; link errors use the same codes as email verification, and password errors come back under `new_password` | `features/auth/api/passwordReset.ts` |
| Google exchange returns tokens plus, optionally, `onboarding_required` (or `tenant_id: null`). Error codes: `email_already_registered` (documented), `email_not_verified`, and `google_session_expired` or any 401 for a sign-in older than 5 min | `features/auth/api/google.ts` |
| allauth reports a failed provider step as `?error=…` on the callback URL (`cancelled` / `access_denied` mean the user cancelled) | `failureFromCallbackParams` in `features/auth/api/google.ts` |
| Onboarding body is `{company_name}` and returns new tokens; 409 / `tenant_already_exists` if a company already exists | `features/auth/api/onboarding.ts` |
| TOTP setup returns `{otpauth_uri, qr_code}`: base64 PNG, with or without a `data:` prefix, plus an optional `secret` (otherwise read from the URI). Confirm `{code}` returns `{backup_codes: [10]}` and optionally a new `access`. Errors: `invalid_code`, and `setup_expired` to restart | `features/auth/api/mfaSetup.ts` |
| **Proposed** `PATCH /auth/me/ {full_name}` → user; `POST /auth/change-password/ {current_password, new_password}` → optional `{access}`, with a wrong current password reported under `current_password`. Isolated so it's easy to change. | `features/profile/api.ts` |
| Password policy: ≥ 10 characters, lower + upper case, a number, a symbol, not the email. The backend's validators remain authoritative. | `lib/passwordPolicy.ts` |

> **Backend note:** for a cross-origin API, the browser only exposes `Retry-After` to
> JavaScript if the response sends `Access-Control-Expose-Headers: Retry-After`
> (django-cors-headers `CORS_EXPOSE_HEADERS`). Without it, countdowns fall back to
> the generic "try again later" message.

> **Google backend note:** the exchange call sends the allauth session cookie
> cross-origin (`withCredentials`), so the API must allow credentials from the portal's
> origin (`CORS_ALLOW_CREDENTIALS`, `CORS_ALLOWED_ORIGINS`), and the portal's
> `/auth/google/callback` must be an allowed allauth callback URL. With the headless
> `post` method, allauth also checks Django's CSRF token: the portal reads the
> `csrftoken` cookie, which only works when API and portal share an origin
> (for example behind a reverse proxy). Otherwise use `VITE_GOOGLE_AUTH_METHOD=get`.

## Routing guards

Guards are layout routes in `src/app/guards.tsx`:

| Guard | Rule |
| --- | --- |
| `PublicOnly` | Signed-in users go to the page they were heading to, or `/dashboard`. |
| `RequireAuth` | Anonymous users go to `/login`; the requested URL is kept and restored after sign-in. It also loads `/auth/me/`. |
| `RequireCompleteAccount` | Mirrors `AccountSetupComplete`: a platform admin without MFA goes to `/mfa/setup`, and a tenant admin without a tenant (Google sign-up) goes to `/onboarding`. |
| `RequireOnboardingPending` | `/onboarding` only for a tenant admin without a company; everyone else goes to `/dashboard`. |
| `RequireMfaSetupPending` | `/mfa/setup` only for a platform admin without MFA. `/auth/me/` is refetched only after "I saved them", so the backup codes stay on screen. |
| `RequireRole` / `RequireTenant` | Otherwise sends the user to `/forbidden`. |

Pages stay mounted while their exit animation plays, and keep re-rendering with
the next location. Guards therefore never redirect from a leaving page
(`useIsPresent`), and the pages that read single-use links strip the query only once,
on mount (`useStripQueryOnMount`). Otherwise they would override the navigation in
progress, for example by dropping a "return to" URL.

The MFA challenge token lives only in memory (`features/auth/mfa/challengeStore.ts`).
Reloading `/login/mfa` shows the expired state.

## Global screens and resilience

| Situation | What the user sees |
| --- | --- |
| Tenant suspended (any request) | `/suspended`; the refresh cookie is dropped so `/login` stays reachable |
| Wrong role / no tenant | `/forbidden` (`RequireRole`, `RequireTenant`) |
| Unknown URL | 404 page |
| Browser offline, or API not answering | a banner at the top, with *Retry* for the API case. Cached data stays on screen and the session is kept |
| Render error in a page | router `errorElement`: reload or go to the dashboard. Technical details appear in dev only |
| Error in the providers themselves | `AppErrorBoundary`, a fallback that needs no context |

## Accessibility

- Every route sets `document.title` ("Sign in · AI Call Center"). After navigation,
  focus moves to the new page's `<main>`, and a *Skip to content* link comes first in
  the tab order.
- One `<h1>` per page, `<h2>` for sections and cards.
- Forms: each control has a `<label>`. Errors are linked with `aria-describedby` and
  announced (`role="alert"`). Banners use `aria-live`: polite for information,
  assertive for errors. The password checklist announces met / not met.
- OTP input: a labelled group, one labelled box per digit, paste and autofill
  (`autocomplete="one-time-code"`), arrows / Home / End / Backspace.
- Dialogs (Radix): focus trap, Escape, focus returned to the trigger. The sign-out
  confirmation starts on "Stay signed in".
- Motion: `MotionConfig reducedMotion="user"`, a CSS `prefers-reduced-motion` rule,
  and the canvas waveform renders a still frame.
- Colour tokens are chosen for 4.5:1 text contrast in both themes. Focus rings are
  always visible.

## Performance

Each page is its own chunk (react-router `lazy`, loaded before the navigation
commits, so there's no extra flash). Vendors are split into long-lived chunks
(`react`, `motion`, `data`, `ui`). The largest is about 68 kB gzipped. MSW and the
devtools are separate chunks that a real build never loads.

## Project structure

```
src/
  app/            router, guards, providers, root layout, paths, safe redirects
  components/
    ui/           shadcn-style primitives + OtpInput, PasswordInput, PasswordStrength,
                  CountdownRing, AnimatedCheck, EnvelopeIllustration, WaveformVisual
    layout/       AuthLayout, BrandPanel, AppHeader
    brand/        Logo
    feedback/     SplashScreen, NetworkBanner, ErrorScreens, FullPageError
    motion/       Stagger, PageTransition, useShake
  features/
    auth/         api, components, hooks, mfa, pages, schemas, session, types
    dashboard/    placeholder dashboard (out of scope; exercises the flow)
    documents/    documents page: upload, table, session links (documents service)
    profile/      account settings: profile, security, session (proposed API)
    system/       global screens (404, 403, suspended)
  i18n/           copy dictionaries (en, fr) + locale detection
  lib/            env, apiClient, errors, forms, networkStatus, passwordPolicy, mask,
                  clipboard, slug, hooks/, auth/{tokenStore,jwt,authEvents}
  mocks/          MSW worker, handlers, in-memory db, devtools, Google chooser
  styles/         design tokens + globals
```
