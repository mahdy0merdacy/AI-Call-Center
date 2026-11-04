import { z } from 'zod';
import { messages } from '@/i18n';
import { meetsPolicy } from '@/lib/passwordPolicy';

const v = messages.validation;

export const emailField = z
  .string()
  .trim()
  .min(1, v.emailRequired)
  .email(v.emailInvalid)
  .transform((value) => value.toLowerCase());

export const loginSchema = z.object({
  email: emailField,
  // No policy check at login: only the server decides what's valid.
  password: z.string().min(1, v.passwordRequired),
});
export type LoginValues = z.infer<typeof loginSchema>;

const rv = messages.registerValidation;

export const registerSchema = z
  .object({
    companyName: z.string().trim().min(1, rv.companyRequired).max(100, rv.companyTooLong),
    email: emailField,
    // Field-level so it shows even while other fields are still invalid.
    password: z.string().refine((value) => meetsPolicy(value), rv.passwordPolicy),
    confirmPassword: z.string().min(1, rv.confirmRequired),
    acceptTerms: z.boolean().refine((value) => value, rv.termsRequired),
  })
  // Cross-field checks (run once every field above is valid).
  .superRefine((values, context) => {
    if (!meetsPolicy(values.password, values.email)) {
      context.addIssue({ code: 'custom', path: ['password'], message: rv.passwordPolicy });
    }
    if (values.confirmPassword && values.confirmPassword !== values.password) {
      context.addIssue({ code: 'custom', path: ['confirmPassword'], message: rv.mismatch });
    }
  });
export type RegisterInput = z.input<typeof registerSchema>;
export type RegisterValues = z.output<typeof registerSchema>;

/** New password + confirmation (reset and change-password). */
export const newPasswordSchema = z
  .object({
    password: z.string().refine((value) => meetsPolicy(value), rv.passwordPolicy),
    confirmPassword: z.string().min(1, rv.confirmRequired),
  })
  .superRefine((values, context) => {
    if (values.confirmPassword !== values.password) {
      context.addIssue({ code: 'custom', path: ['confirmPassword'], message: rv.mismatch });
    }
  });
export type NewPasswordValues = z.infer<typeof newPasswordSchema>;

export const emailOnlySchema = z.object({ email: emailField });
export type EmailOnlyValues = z.output<typeof emailOnlySchema>;

export const totpCodeSchema = z.string().regex(/^\d{6}$/, v.codeLength);

/** Backup codes: 8 alphanumerics, dash and spaces optional (K7PX-M2QD). */
export const backupCodeSchema = z.object({
  code: z
    .string()
    .transform((value) => value.replace(/[\s-]/g, '').toUpperCase())
    .pipe(z.string().regex(/^[A-Z0-9]{8}$/, v.backupCodeInvalid)),
});
export type BackupCodeValues = z.input<typeof backupCodeSchema>;
