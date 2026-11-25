/**
 * Mock versions of DRF throttling and django-axes lockout. In memory only:
 * a reload clears every counter, which is handy while demoing.
 */
const windows = new Map<string, number[]>();

/** Sliding-window rate limit. Returns seconds to wait, or 0 when allowed. */
export function throttle(key: string, limit: number, windowSeconds: number): number {
  const now = Date.now();
  const windowMs = windowSeconds * 1000;
  const hits = (windows.get(key) ?? []).filter((time) => now - time < windowMs);
  if (hits.length >= limit) {
    windows.set(key, hits);
    const oldest = hits[0] ?? now;
    return Math.max(1, Math.ceil((oldest + windowMs - now) / 1000));
  }
  hits.push(now);
  windows.set(key, hits);
  return 0;
}

interface Counter {
  failures: number;
  lockedUntil: number;
}

const counters = new Map<string, Counter>();

/** Seconds left on an active lock for `key`, or 0. */
export function lockRemaining(key: string): number {
  const counter = counters.get(key);
  if (!counter || counter.lockedUntil <= Date.now()) return 0;
  return Math.ceil((counter.lockedUntil - Date.now()) / 1000);
}

/** Records a failure; locks for `lockSeconds` once `threshold` is reached. Returns true if now locked. */
export function recordFailure(key: string, threshold: number, lockSeconds: number): boolean {
  const counter = counters.get(key) ?? { failures: 0, lockedUntil: 0 };
  counter.failures += 1;
  if (counter.failures >= threshold) {
    counter.failures = 0;
    counter.lockedUntil = Date.now() + lockSeconds * 1000;
  }
  counters.set(key, counter);
  return counter.lockedUntil > Date.now();
}

export function resetFailures(key: string): void {
  counters.delete(key);
}
