import { z } from 'zod';
import { messages } from '@/i18n';
import { meetsPolicy } from '@/lib/passwordPolicy';

const p = messages.profile;
const rv = messages.registerValidation;

export const profileSchema = z.object({
  fullName: z.string().trim().max(150, p.fullNameTooLong),
});
export type ProfileValues = z.infer<typeof profileSchema>;

export const changePasswordSchema = z
  .object({
    currentPassword: z.string().min(1, p.currentRequired),
    password: z.string().refine((value) => meetsPolicy(value), rv.passwordPolicy),
    confirmPassword: z.string().min(1, rv.confirmRequired),
  })
  .superRefine((values, context) => {
    if (values.password === values.currentPassword) {
      context.addIssue({ code: 'custom', path: ['password'], message: p.sameAsCurrent });
    }
    if (values.confirmPassword !== values.password) {
      context.addIssue({ code: 'custom', path: ['confirmPassword'], message: rv.mismatch });
    }
  });
export type ChangePasswordValues = z.infer<typeof changePasswordSchema>;
