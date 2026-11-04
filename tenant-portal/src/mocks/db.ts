/**
 * In-memory mock backend state. Persisted to sessionStorage so a page reload
 * behaves like a real server (the session survives). MOCK ONLY: the real
 * refresh token lives in an HttpOnly cookie the frontend cannot read.
 */
import type { AuthProvider, Role } from '@/features/auth/types';
import { resetDocumentsStore } from './documentsStore';

export interface MockUser {
  id: string;
  email: string;
  password: string | null;
  full_name: string | null;
  role: Role;
  is_email_verified: boolean;
  tenant_id: string | null;
  mfa_enabled: boolean;
  auth_provider: AuthProvider;
  date_joined: string;
  /** Google's stable subject id: Google users are matched on this, never on email. */
  google_sub?: string;
}

export type MockGoogleScenario = 'new' | 'existing' | 'conflict' | 'unverified' | 'expired';

/** What allauth's session cookie would hold after the Google round trip. */
export interface MockGoogleSession {
  scenario: MockGoogleScenario;
  sub: string;
  email: string;
  name: string;
  createdAt: number;
}

export interface MockTenant {
  id: string;
  name: string;
  slug: string;
  status: 'active' | 'suspended';
  created_at: string;
}

export interface MockEmailToken {
  purpose: 'verify' | 'reset';
  userId: string;
  expiresAt: number;
  used: boolean;
}

interface MockState {
  users: MockUser[];
  tenants: MockTenant[];
  /** refresh token id -> user id */
  sessions: Record<string, string>;
  /** user id -> unused backup codes (normalised, no dash). */
  backupCodes: Record<string, string[]>;
  /** Single-use email links (verification: 24 h, password reset: 1 h). */
  emailTokens: Record<string, MockEmailToken>;
  /** The "cookie" currently held by the browser. */
  refreshCookie: string | null;
  /** allauth session after Google redirected back (consumed by the exchange). */
  googleSession: MockGoogleSession | null;
}

// Bump when the seed shape changes so stale sessionStorage state is ignored.
const STORAGE_KEY = 'msw:state:v5';

/** Valid TOTP for every MFA-enrolled mock user (a real authenticator rotates it). */
export const DEMO_TOTP = '123456';

/** Shared demo password for every seeded password account (mock only). */
export const DEMO_PASSWORD = 'Demo!Pass2024';

function tenantAdmin(
  id: string,
  email: string,
  fullName: string,
  tenantId: string,
  joined: string,
): MockUser {
  return {
    id,
    email,
    password: DEMO_PASSWORD,
    full_name: fullName,
    role: 'tenant_admin',
    is_email_verified: true,
    tenant_id: tenantId,
    mfa_enabled: false,
    auth_provider: 'password',
    date_joined: joined,
  };
}

function seed(): MockState {
  return {
    // Ids, names and slugs mirror tenants/*.yaml (tenant_id / display_name / slug).
    tenants: [
      {
        id: 'acme-delivery',
        name: 'Acme Delivery',
        slug: 'acme',
        status: 'active',
        created_at: '2026-09-01T09:00:00Z',
      },
      {
        id: 'swiftnet-isp',
        name: 'SwiftNet Broadband',
        slug: 'swiftnet',
        status: 'active',
        created_at: '2026-09-03T09:00:00Z',
      },
      {
        id: 'maison-verte',
        name: 'Maison Verte',
        slug: 'maisonverte',
        status: 'active',
        created_at: '2026-09-05T09:00:00Z',
      },
      // Mock-only tenant for the "suspended mid-session" scenario.
      {
        id: 'demo-suspended',
        name: 'Demo Suspended Co',
        slug: 'suspended',
        status: 'suspended',
        created_at: '2026-08-20T09:00:00Z',
      },
    ],
    users: [
      tenantAdmin('u-acme', 'admin@acme.test', 'Amira Haddad', 'acme-delivery', '2026-09-01T09:00:00Z'),
      tenantAdmin('u-swiftnet', 'admin@swiftnet.test', 'Daniel Okafor', 'swiftnet-isp', '2026-09-03T09:00:00Z'),
      tenantAdmin('u-maisonverte', 'admin@maisonverte.test', 'Camille Moreau', 'maison-verte', '2026-09-05T09:00:00Z'),
      tenantAdmin('u-suspended', 'admin@suspended.test', 'Louis Bernard', 'demo-suspended', '2026-08-20T09:00:00Z'),
      {
        ...tenantAdmin('u-unverified', 'unverified@acme.test', 'Hugo Petit', 'acme-delivery', '2026-10-30T09:00:00Z'),
        is_email_verified: false,
      },
      {
        id: 'u-google',
        email: 'lea.martin@gmail.test',
        password: null,
        full_name: 'Léa Martin',
        role: 'tenant_admin',
        is_email_verified: true,
        tenant_id: 'maison-verte',
        mfa_enabled: false,
        auth_provider: 'google',
        date_joined: '2026-09-12T08:30:00Z',
        google_sub: 'google-sub-lea-martin',
      },
      {
        id: 'u-platform-new',
        email: 'platform-new@aicc.test',
        password: DEMO_PASSWORD,
        full_name: 'Noah Girard',
        role: 'platform_admin',
        is_email_verified: true,
        tenant_id: null,
        mfa_enabled: false,
        auth_provider: 'password',
        date_joined: '2026-10-28T10:00:00Z',
      },
      {
        id: 'u-platform',
        email: 'platform@aicc.test',
        password: DEMO_PASSWORD,
        full_name: 'Sofia Laurent',
        role: 'platform_admin',
        is_email_verified: true,
        tenant_id: null,
        mfa_enabled: true,
        auth_provider: 'password',
        date_joined: '2024-11-02T10:00:00Z',
      },
    ],
    sessions: {},
    backupCodes: {
      'u-platform': [
        'K7PXM2QD', 'R4TW9BNE', 'H8LZ3CVA', 'M2QY7XKD', 'P9DN4RFT',
        'W3JB6HSU', 'C5VK8GLE', 'T6NR2WQZ', 'Y4FA9MPB', 'E7XS3JDH',
      ],
    },
    emailTokens: {},
    refreshCookie: null,
    googleSession: null,
  };
}

/** The `uid` in emailed links: base64url of the user id, like Django's urlsafe_base64_encode. */
export function encodeUid(userId: string): string {
  return btoa(userId).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Creates a single-use email token and returns the link path for it. */
export function issueEmailToken(purpose: MockEmailToken['purpose'], userId: string, ttlSeconds: number): string {
  const token = crypto.randomUUID().replace(/-/g, '');
  db.emailTokens[token] = { purpose, userId, expiresAt: Date.now() + ttlSeconds * 1000, used: false };
  persist();
  const path = purpose === 'verify' ? '/verify-email' : '/reset-password';
  return `${path}?uid=${encodeUid(userId)}&token=${token}`;
}

export function findUserByEmail(email: string): MockUser | undefined {
  const normalized = email.trim().toLowerCase();
  return db.users.find((user) => user.email === normalized);
}

function load(): MockState {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as MockState;
  } catch {
    // Corrupted or unavailable storage: start from the seed.
  }
  return seed();
}

export const db: MockState = load();

export function persist(): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(db));
  } catch {
    // Ignore quota/private-mode errors in the mock.
  }
}

export function resetMockDb(): void {
  Object.assign(db, seed());
  persist();
  resetDocumentsStore();
}

export function findUserById(id: string): MockUser | undefined {
  return db.users.find((user) => user.id === id);
}

export function findTenant(id: string | null): MockTenant | undefined {
  return id ? db.tenants.find((tenant) => tenant.id === id) : undefined;
}

export function randomId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

/** Issues a new refresh "cookie" for the user (rotation-aware). */
export function startSession(userId: string): void {
  const refreshId = randomId('rt');
  db.sessions[refreshId] = userId;
  db.refreshCookie = refreshId;
  persist();
}

/** Rotates the current refresh cookie; returns the user id or null if invalid. */
export function rotateSession(): string | null {
  const current = db.refreshCookie;
  const userId = current ? db.sessions[current] : undefined;
  if (!current || !userId) return null;
  delete db.sessions[current]; // blacklist the old one
  startSession(userId);
  return userId;
}

export function endSession(): void {
  if (db.refreshCookie) delete db.sessions[db.refreshCookie];
  db.refreshCookie = null;
  persist();
}

/** Revokes every session of a user (password reset / change). */
export function revokeUserSessions(userId: string): void {
  for (const [refreshId, owner] of Object.entries(db.sessions)) {
    if (owner === userId) delete db.sessions[refreshId];
  }
  persist();
}

// Handy from the devtools console: `window.__mockDb.reset()`.
declare global {
  interface Window {
    __mockDb?: { state: MockState; reset: () => void };
  }
}
window.__mockDb = { state: db, reset: resetMockDb };
