/**
 * Client-side password policy shown as a live checklist. The backend's
 * validators stay authoritative: anything they reject comes back as a field
 * error and is shown under the password field.
 *
 * Assumption (backend docs are silent): ≥ 10 chars, lower + upper case,
 * a number and a symbol, and not the email itself.
 */
import { messages } from '@/i18n';

export const PASSWORD_MIN_LENGTH = 10;

export type PasswordRuleId = 'minLength' | 'lowercase' | 'uppercase' | 'number' | 'symbol' | 'notEmail';

export interface PasswordRule {
  id: PasswordRuleId;
  label: string;
  test: (password: string, email: string) => boolean;
}

const p = messages.passwordPolicy;

export const passwordRules: PasswordRule[] = [
  { id: 'minLength', label: p.minLength(PASSWORD_MIN_LENGTH), test: (pw) => pw.length >= PASSWORD_MIN_LENGTH },
  { id: 'lowercase', label: p.lowercase, test: (pw) => /[a-z]/.test(pw) },
  { id: 'uppercase', label: p.uppercase, test: (pw) => /[A-Z]/.test(pw) },
  { id: 'number', label: p.number, test: (pw) => /\d/.test(pw) },
  { id: 'symbol', label: p.symbol, test: (pw) => /[^A-Za-z0-9\s]/.test(pw) },
  {
    id: 'notEmail',
    label: p.notEmail,
    test: (pw, email) => {
      if (!pw) return false;
      const local = email.split('@')[0]?.toLowerCase() ?? '';
      const lower = pw.toLowerCase();
      return !email || (lower !== email.toLowerCase() && (local.length < 4 || !lower.includes(local)));
    },
  },
];

export function meetsPolicy(password: string, email = ''): boolean {
  return passwordRules.every((rule) => rule.test(password, email));
}

const COMMON_FRAGMENTS = ['password', 'motdepasse', 'azerty', 'qwerty', '123456', 'admin', 'welcome', 'letmein'];

/**
 * 0–4 strength estimate (no dictionary download): rules met, length
 * bonuses, penalties for common fragments and repeated characters.
 */
export function passwordStrength(password: string, email = ''): 0 | 1 | 2 | 3 | 4 {
  if (!password) return 0;
  const met = passwordRules.filter((rule) => rule.test(password, email)).length;
  let score = met - 2; // 6 rules → up to 4
  if (password.length >= 14) score += 1;
  if (password.length >= 18) score += 1;
  const lower = password.toLowerCase();
  if (COMMON_FRAGMENTS.some((fragment) => lower.includes(fragment))) score -= 3;
  if (/(.)\1{2,}/.test(password)) score -= 1;
  if (!meetsPolicy(password, email)) score = Math.min(score, 1);
  return Math.max(0, Math.min(4, score)) as 0 | 1 | 2 | 3 | 4;
}
