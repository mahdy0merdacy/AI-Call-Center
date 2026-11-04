/**
 * Mock state for session links, kept per tenant like the real service scopes
 * them by the token's tenant. Persisted to sessionStorage across reloads.
 */
export interface MockSessionLink {
  id: string;
  tenant_id: string;
  token: string;
  url: string;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
}

type LinksState = Record<string, MockSessionLink[]>;

// Bump when the seed shape changes so stale sessionStorage state is ignored.
const STORAGE_KEY = 'msw:session-links:v1';

// Fake host: clicking a mock link goes nowhere on purpose.
const LINK_BASE_URL = 'https://voice.aicallcenter.test/session';

/** 43 URL-safe characters from 32 random bytes, like `secrets.token_urlsafe(32)`. */
function newToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

const ago = (minutes: number): string => new Date(Date.now() - minutes * 60_000).toISOString();

function link(tenantId: string, createdMinutesAgo: number, active: boolean, expiresAt: string | null): MockSessionLink {
  const token = newToken();
  return {
    id: crypto.randomUUID(),
    tenant_id: tenantId,
    token,
    url: `${LINK_BASE_URL}/${token}`,
    is_active: active,
    expires_at: expiresAt,
    created_at: ago(createdMinutesAgo),
  };
}

/** Acme starts with one link of each kind; the other tenants start empty. */
function seed(): LinksState {
  return {
    'acme-delivery': [
      link('acme-delivery', 60 * 2, true, null),
      link('acme-delivery', 60 * 24 * 3, true, ago(-60 * 24 * 7)),
      link('acme-delivery', 60 * 24 * 10, false, null),
      // Expired by time: the service never refuses it, the portal derives "expired".
      link('acme-delivery', 60 * 24 * 14, true, ago(60 * 24)),
    ],
    'swiftnet-isp': [],
    'maison-verte': [],
  };
}

function load(): LinksState {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as LinksState;
  } catch {
    // Fall through to a fresh seed.
  }
  return seed();
}

const state: LinksState = load();

function persist(): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Ignore quota/private-mode errors in the mock.
  }
}

/** The tenant's links, newest first (the real service orders by -created_at). */
export function listTenantLinks(tenantId: string): MockSessionLink[] {
  return [...(state[tenantId] ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function addTenantLink(tenantId: string, expiresAt: string | null): MockSessionLink {
  const created = link(tenantId, 0, true, expiresAt);
  (state[tenantId] ??= []).push(created);
  persist();
  return created;
}

/** Another tenant's link is "not found", exactly like one that doesn't exist. */
export function findTenantLink(tenantId: string, id: string): MockSessionLink | undefined {
  return state[tenantId]?.find((item) => item.id === id);
}

export function persistLinks(): void {
  persist();
}

export function resetSessionLinksStore(): void {
  for (const key of Object.keys(state)) delete state[key];
  Object.assign(state, seed());
  persist();
}
