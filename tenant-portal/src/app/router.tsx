import { Navigate, createBrowserRouter, type RouteObject } from 'react-router-dom';
import { RouteErrorPage } from '@/components/feedback/ErrorScreens';
import { NotFoundPage } from '@/features/system/pages/NotFoundPage';
import { env } from '@/lib/env';
import {
  PublicOnly,
  RequireAuth,
  RequireCompleteAccount,
  RequireMfaSetupPending,
  RequireOnboardingPending,
  RequireRole,
  RequireTenant,
} from './guards';
import { paths } from './paths';
import { RootLayout } from './RootLayout';

/*
 * Pages are code-split with the data router's `lazy`: the chunk loads before
 * the navigation commits, so there's no extra loading flash between pages.
 * Guards stay in the main bundle (they're tiny and needed on every route).
 */

function MockCrash(): never {
  throw new Error('Simulated render error (mock devtools)');
}

// Mock mode only: lets the devtools panel demo the error screen.
const mockRoutes: RouteObject[] = env.enableMocks ? [{ path: '/__mock/crash', Component: MockCrash }] : [];

export const router = createBrowserRouter([
  {
    element: <RootLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { path: paths.home, element: <Navigate to={paths.dashboard} replace /> },

      {
        element: <PublicOnly />,
        children: [
          {
            path: paths.login,
            lazy: () => import('@/features/auth/pages/LoginPage').then((m) => ({ Component: m.LoginPage })),
          },
          {
            path: paths.loginMfa,
            lazy: () =>
              import('@/features/auth/pages/MfaChallengePage').then((m) => ({ Component: m.MfaChallengePage })),
          },
          {
            path: paths.register,
            lazy: () => import('@/features/auth/pages/RegisterPage').then((m) => ({ Component: m.RegisterPage })),
          },
          {
            path: paths.forgotPassword,
            lazy: () =>
              import('@/features/auth/pages/ForgotPasswordPage').then((m) => ({ Component: m.ForgotPasswordPage })),
          },
        ],
      },

      // Email links work signed in or not: they may be opened in any browser.
      {
        path: paths.verifyEmail,
        lazy: () => import('@/features/auth/pages/VerifyEmailPage').then((m) => ({ Component: m.VerifyEmailPage })),
      },
      {
        path: paths.resetPassword,
        lazy: () =>
          import('@/features/auth/pages/ResetPasswordPage').then((m) => ({ Component: m.ResetPasswordPage })),
      },
      {
        path: paths.googleCallback,
        lazy: () =>
          import('@/features/auth/pages/GoogleCallbackPage').then((m) => ({ Component: m.GoogleCallbackPage })),
      },

      {
        element: <RequireAuth />,
        children: [
          {
            element: <RequireOnboardingPending />,
            children: [
              {
                path: paths.onboarding,
                lazy: () =>
                  import('@/features/auth/pages/OnboardingPage').then((m) => ({ Component: m.OnboardingPage })),
              },
            ],
          },
          {
            element: <RequireMfaSetupPending />,
            children: [
              {
                path: paths.mfaSetup,
                lazy: () => import('@/features/auth/pages/MfaSetupPage').then((m) => ({ Component: m.MfaSetupPage })),
              },
            ],
          },
          {
            element: <RequireCompleteAccount />,
            children: [
              {
                path: paths.dashboard,
                lazy: () =>
                  import('@/features/dashboard/pages/DashboardPage').then((m) => ({ Component: m.DashboardPage })),
              },
              {
                // The documents service only accepts company admins (it answers 403 to anyone else).
                element: <RequireRole role="tenant_admin" />,
                children: [
                  {
                    element: <RequireTenant />,
                    children: [
                      {
                        path: paths.documents,
                        lazy: () =>
                          import('@/features/documents/pages/DocumentsPage').then((m) => ({
                            Component: m.DocumentsPage,
                          })),
                      },
                    ],
                  },
                ],
              },
              {
                path: paths.profile,
                lazy: () => import('@/features/profile/pages/ProfilePage').then((m) => ({ Component: m.ProfilePage })),
              },
            ],
          },
          {
            element: <RequireRole role="platform_admin" />,
            children: [
              {
                path: paths.adminTenants,
                lazy: () =>
                  import('@/features/admin/pages/TenantsPage').then((m) => ({ Component: m.TenantsPage })),
              },
            ],
          },
        ],
      },

      {
        path: paths.suspended,
        lazy: () => import('@/features/system/pages/SuspendedPage').then((m) => ({ Component: m.SuspendedPage })),
      },
      {
        path: paths.forbidden,
        lazy: () => import('@/features/system/pages/ForbiddenPage').then((m) => ({ Component: m.ForbiddenPage })),
      },
      ...mockRoutes,
      // Eager: the error screen also renders it, so it is in the main bundle anyway.
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);
