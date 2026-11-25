import type { LoginNotice } from '@/app/paths';
import { Alert } from '@/components/ui/alert';
import { messages } from '@/i18n';

const variants: Record<LoginNotice, 'info' | 'success' | 'warning'> = {
  session_expired: 'warning',
  signed_out: 'info',
  password_reset: 'success',
  email_verified: 'success',
  mfa_expired: 'warning',
};

/** Banner for why the user landed on /login (passed through router state). */
export function LoginNoticeBanner({ notice }: { notice: LoginNotice }) {
  const copy = messages.login.notices[notice];
  return (
    <Alert variant={variants[notice]} title={copy.title}>
      {copy.body}
    </Alert>
  );
}
