import { useEffect, useRef, useState } from 'react';

function secondsUntil(endsAt: number | null): number {
  return endsAt === null ? 0 : Math.max(0, Math.ceil((endsAt - Date.now()) / 1000));
}

/**
 * Seconds left until `endsAt` (epoch ms), ticking every 250 ms so the display
 * stays in step with the wall clock even if the tab was throttled.
 * `onComplete` fires once when it reaches zero.
 */
export function useCountdown(endsAt: number | null, onComplete?: () => void): number {
  const [secondsLeft, setSecondsLeft] = useState(() => secondsUntil(endsAt));
  const completeRef = useRef(onComplete);
  completeRef.current = onComplete;

  useEffect(() => {
    setSecondsLeft(secondsUntil(endsAt));
    if (endsAt === null) return;
    let fired = false;
    const id = window.setInterval(() => {
      const left = secondsUntil(endsAt);
      setSecondsLeft(left);
      if (left === 0 && !fired) {
        fired = true;
        window.clearInterval(id);
        completeRef.current?.();
      }
    }, 250);
    return () => window.clearInterval(id);
  }, [endsAt]);

  return secondsLeft;
}

export function formatClock(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const mm = String(minutes).padStart(hours > 0 ? 2 : 1, '0');
  const ss = String(seconds).padStart(2, '0');
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}
