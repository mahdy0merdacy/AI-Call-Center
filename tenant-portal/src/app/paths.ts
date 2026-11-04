/** Every route path in one place so redirects can't drift from the router. */
export const paths = {
  home: '/',
  login: '/login',
  loginMfa: '/login/mfa',
  register: '/register',
  verifyEmail: '/verify-email',
  forgotPassword: '/forgot-password',
  resetPassword: '/reset-password',
  googleCallback: '/auth/google/callback',
  onboarding: '/onboarding',
  mfaSetup: '/mfa/setup',
  dashboard: '/dashboard',
  profile: '/profile',
  adminTenants: '/admin/tenants',
  suspended: '/suspended',
  forbidden: '/forbidden',
} as const;

/** Banners the login page can show, passed through router state. */
export type LoginNotice =
  | 'session_expired'
  | 'signed_out'
  | 'password_reset'
  | 'email_verified'
  | 'mfa_expired';

export interface LoginLocationState {
  notice?: LoginNotice;
  /** Where to go after signing in (pathname + search). */
  from?: string;
}
