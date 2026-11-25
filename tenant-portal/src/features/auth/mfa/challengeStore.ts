/**
 * The pending MFA challenge between login step 1 and step 2. Memory only:
 * the mfa_token is never put in storage or in the URL, so a reload of
 * /login/mfa lands on the "expired" state and the user signs in again.
 */
import { MFA_MAX_ATTEMPTS } from '../api/login';

export interface MfaChallenge {
  mfaToken: string;
  /** Epoch ms. */
  expiresAt: number;
  totalSeconds: number;
  attemptsRemaining: number;
  /** Where to go after verification. */
  from: string | undefined;
}

let challenge: MfaChallenge | null = null;

export const mfaChallengeStore = {
  start(mfaToken: string, expiresInSeconds: number, from: string | undefined): void {
    challenge = {
      mfaToken,
      expiresAt: Date.now() + expiresInSeconds * 1000,
      totalSeconds: expiresInSeconds,
      attemptsRemaining: MFA_MAX_ATTEMPTS,
      from,
    };
  },
  get(): MfaChallenge | null {
    return challenge;
  },
  setAttemptsRemaining(count: number): void {
    if (challenge) challenge = { ...challenge, attemptsRemaining: Math.max(0, count) };
  },
  clear(): void {
    challenge = null;
  },
};
