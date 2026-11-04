import { Alert } from '@/components/ui/alert';
import { CountdownRing } from '@/components/ui/CountdownRing';
import { messages } from '@/i18n';

interface LockoutAlertProps {
  /** Epoch ms when the lock ends, or null when the server gave no Retry-After. */
  until: number | null;
  totalSeconds: number;
  title?: string;
  /** Overrides the default sign-in wording (timer / no-timer variants). */
  body?: string;
  onExpire: () => void;
}

/** Friendly lockout / throttling message with an animated countdown when the duration is known. */
export function LockoutAlert({ until, totalSeconds, title, body, onExpire }: LockoutAlertProps) {
  return (
    <Alert
      variant="warning"
      live="assertive"
      title={title ?? messages.login.lockedTitle}
      aside={
        until !== null ? (
          <CountdownRing endsAt={until} totalSeconds={totalSeconds} tone="warning" onComplete={onExpire} />
        ) : undefined
      }
    >
      {body ?? (until !== null ? messages.login.lockedBody : messages.login.lockedNoTimer)}
    </Alert>
  );
}
