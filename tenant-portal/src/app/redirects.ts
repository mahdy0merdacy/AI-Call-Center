import { paths } from './paths';

const AUTH_ENTRY_PATHS: string[] = [paths.login, paths.loginMfa, paths.register];

/**
 * Validates a "return to" target: same-origin paths only (no `//host` or
 * `https://` open redirects) and never back into the sign-in pages.
 */
export function safeReturnTo(target: string | undefined | null): string {
  if (!target || !target.startsWith('/') || target.startsWith('//') || target.startsWith('/\\')) {
    return paths.dashboard;
  }
  const pathname = target.split(/[?#]/)[0] ?? '';
  if (AUTH_ENTRY_PATHS.includes(pathname) || pathname === paths.home) return paths.dashboard;
  return target;
}
