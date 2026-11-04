import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios';
import { z } from 'zod';
import { messages } from '@/i18n';
import { authEvents } from '@/lib/auth/authEvents';
import { tokenStore } from '@/lib/auth/tokenStore';
import { env } from '@/lib/env';
import { ApiRequestError, toApiError } from '@/lib/errors';
import { networkStatus } from '@/lib/networkStatus';

declare module 'axios' {
  interface AxiosRequestConfig {
    /** Don't try refresh-and-replay on 401 (login, MFA, refresh itself…). */
    skipAuthRefresh?: boolean;
    /** Internal: set once a request has been replayed after a refresh. */
    _retried?: boolean;
  }
}

/**
 * Required on every call that relies on the HttpOnly refresh cookie
 * (refresh, logout, Google exchange): send cookies cross-origin and the
 * custom header the backend uses as CSRF protection.
 */
export const cookieAuthConfig = {
  withCredentials: true,
  headers: { 'X-Requested-With': 'XMLHttpRequest' },
} satisfies AxiosRequestConfig;

const baseConfig: AxiosRequestConfig = {
  baseURL: env.apiRoot,
  timeout: 20_000,
  headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
};

/**
 * Any HTTP answer (even 4xx/5xx) proves the server is reachable; only a
 * missing response (network error, timeout) means it isn't. Feeds the
 * offline / unreachable banner.
 */
function trackReachability(client: AxiosInstance) {
  client.interceptors.response.use(
    (response) => {
      networkStatus.markReachable();
      return response;
    },
    (error: unknown) => {
      if (axios.isAxiosError(error) && !error.response) networkStatus.markUnreachable();
      else networkStatus.markReachable();
      return Promise.reject(error);
    },
  );
}

/** Bare client: no auth header, no refresh logic. Used for the refresh call itself. */
const rawClient: AxiosInstance = axios.create(baseConfig);
trackReachability(rawClient);
rawClient.interceptors.response.use(undefined, (error: unknown) =>
  Promise.reject(toApiError(error)),
);

/** Application client: bearer token, silent refresh, normalised errors. */
export const api: AxiosInstance = axios.create(baseConfig);
trackReachability(api);

/* ------------------------------------------------------------------ */
/* Token responses                                                     */
/* ------------------------------------------------------------------ */

const tokenResponseSchema = z.union([
  z.object({ access: z.string().min(1) }).passthrough(),
  z
    .object({ access_token: z.string().min(1) })
    .passthrough()
    .transform((value) => ({ ...value, access: value.access_token })),
]);

/** Accepts SimpleJWT's `{access}` (or `{access_token}`) and returns the token. */
export function extractAccessToken(data: unknown): string {
  const parsed = tokenResponseSchema.safeParse(data);
  if (!parsed.success) {
    throw new ApiRequestError({
      kind: 'unknown',
      status: null,
      code: 'invalid_token_response',
      message: messages.errors.invalidTokenResponse,
    });
  }
  return parsed.data.access;
}

/* ------------------------------------------------------------------ */
/* Suspension detection                                                */
/* ------------------------------------------------------------------ */

const SUSPENDED_CODES = ['tenant_suspended', 'account_suspended'];

export function isSuspendedError(error: unknown): boolean {
  const apiError = toApiError(error);
  return (
    (apiError.status === 401 || apiError.status === 403) && apiError.hasCode(...SUSPENDED_CODES)
  );
}

/* ------------------------------------------------------------------ */
/* Refresh (single flight)                                             */
/* ------------------------------------------------------------------ */

let inflightRefresh: Promise<string> | null = null;

async function performRefresh(): Promise<string> {
  const response = await rawClient.post<unknown>('/auth/refresh/', null, cookieAuthConfig);
  const token = extractAccessToken(response.data);
  tokenStore.set(token);
  return token;
}

/**
 * Rotates the refresh cookie and stores the new access token.
 *
 * The backend blacklists the previous refresh token on every rotation, so two
 * concurrent calls would log the user out. All callers in this tab share one
 * in-flight promise, and the Web Locks API serialises refreshes across tabs
 * (each tab then sends the latest cookie).
 */
export function refreshAccessToken(): Promise<string> {
  if (!inflightRefresh) {
    const run =
      typeof navigator !== 'undefined' && 'locks' in navigator
        ? navigator.locks.request('tp-auth-refresh', performRefresh)
        : performRefresh();
    inflightRefresh = run.finally(() => {
      inflightRefresh = null;
    });
  }
  return inflightRefresh;
}

/**
 * Called when a refresh fails while the user was signed in: drop the session
 * and tell the router whether this is a suspension or a plain expiry.
 */
export function handleRefreshFailure(error: unknown): void {
  const apiError = toApiError(error);
  // A network blip or 5xx doesn't mean the session is gone: keep it and let
  // the next request try again.
  if (isTransient(apiError)) return;
  const hadSession = tokenStore.get() !== null;
  tokenStore.clear();
  if (isSuspendedError(apiError)) {
    authEvents.emit('tenant-suspended');
  } else if (hadSession) {
    authEvents.emit('session-expired');
  }
}

/* ------------------------------------------------------------------ */
/* Interceptors                                                        */
/* ------------------------------------------------------------------ */

function bearerFrom(config: AxiosRequestConfig): string | null {
  const header: unknown = config.headers?.Authorization ?? config.headers?.authorization;
  return typeof header === 'string' && header.startsWith('Bearer ') ? header.slice(7) : null;
}

api.interceptors.request.use((config) => {
  const token = tokenStore.get();
  if (token && !config.headers.has('Authorization')) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

api.interceptors.response.use(undefined, async (error: unknown) => {
  if (!axios.isAxiosError(error) || !error.config) {
    return Promise.reject(toApiError(error));
  }

  const config = error.config;
  const apiError = toApiError(error);

  // Tenant suspended mid-session: the backend blocks immediately, even with a valid token.
  if (isSuspendedError(apiError) && bearerFrom(config)) {
    tokenStore.clear();
    authEvents.emit('tenant-suspended');
    return Promise.reject(apiError);
  }

  if (apiError.status !== 401 || config.skipAuthRefresh || config._retried) {
    return Promise.reject(apiError);
  }

  const sentToken = bearerFrom(config);
  if (!sentToken) {
    // Anonymous request rejected: nothing to refresh.
    return Promise.reject(apiError);
  }

  config._retried = true;

  let token: string;
  try {
    // Another request may already have rotated the token while this one was in flight.
    const current = tokenStore.get();
    token = current && current !== sentToken ? current : await refreshAccessToken();
  } catch (refreshError) {
    const refreshApiError = toApiError(refreshError);
    handleRefreshFailure(refreshApiError);
    return Promise.reject(isTransient(refreshApiError) ? refreshApiError : apiError);
  }

  config.headers.set('Authorization', `Bearer ${token}`);
  // A failure of the replay goes through this interceptor again (already `_retried`).
  return api.request(config);
});

function isTransient(error: ApiRequestError): boolean {
  return error.kind === 'network' || error.kind === 'timeout' || error.kind === 'server';
}
