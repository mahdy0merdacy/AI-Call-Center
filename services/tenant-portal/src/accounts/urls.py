from django.urls import path

from accounts.views import auth, google, mfa, tenants

auth_urlpatterns = [
    path("register/", auth.RegisterView.as_view(), name="register"),
    path("verify-email/", auth.VerifyEmailView.as_view(), name="verify-email"),
    path(
        "resend-verification/",
        auth.ResendVerificationView.as_view(),
        name="resend-verification",
    ),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("refresh/", auth.RefreshView.as_view(), name="refresh"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password-reset/", auth.PasswordResetRequestView.as_view(), name="password-reset"),
    path(
        "password-reset/confirm/",
        auth.PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("me/", auth.MeView.as_view(), name="me"),
    path("mfa/totp/setup/", mfa.TOTPSetupView.as_view(), name="mfa-totp-setup"),
    path("mfa/totp/confirm/", mfa.TOTPConfirmView.as_view(), name="mfa-totp-confirm"),
    path("mfa/verify/", mfa.MFAVerifyView.as_view(), name="mfa-verify"),
    path("google/exchange/", google.GoogleExchangeView.as_view(), name="google-exchange"),
    path("onboarding/", google.OnboardingView.as_view(), name="onboarding"),
]

admin_urlpatterns = [
    path("tenants/", tenants.AdminTenantListView.as_view(), name="tenant-list"),
    path(
        "tenants/<uuid:tenant_id>/suspend/",
        tenants.AdminTenantSuspendView.as_view(),
        name="tenant-suspend",
    ),
]

tenant_urlpatterns = [
    path("me/", tenants.TenantMeView.as_view(), name="me"),
]
