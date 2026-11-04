import { useCallback, useEffect, useState } from 'react';
import { useCountdown } from './useCountdown';

export const RESEND_COOLDOWN_SECONDS = 60;

/** key -> epoch ms. Module-level so a cooldown survives closing a modal or changing page. */
const cooldowns = new Map<string, number>();

/** Starts a cooldown from outside the component that displays it (e.g. right after sign-up). */
export function startCooldown(key: string, seconds: number = RESEND_COOLDOWN_SECONDS): void {
  cooldowns.set(key, Date.now() + seconds * 1000);
}

function activeUntil(key: string): number | null {
  const until = cooldowns.get(key);
  return until !== undefined && until > Date.now() ? until : null;
}

/**
 * Resend cooldown keyed by e.g. `verify:alice@acme.test`, so each address has
 * its own timer and reopening the modal doesn't reset it.
 */
export function useCooldown(key: string) {
  const [endsAt, setEndsAt] = useState<number | null>(() => activeUntil(key));

  useEffect(() => {
    setEndsAt(activeUntil(key));
  }, [key]);

  const secondsLeft = useCountdown(endsAt, () => setEndsAt(null));

  const start = useCallback(
    (seconds: number = RESEND_COOLDOWN_SECONDS) => {
      const until = Date.now() + seconds * 1000;
      cooldowns.set(key, until);
      setEndsAt(until);
    },
    [key],
  );

  return { endsAt, secondsLeft, active: endsAt !== null && secondsLeft > 0, start };
}
