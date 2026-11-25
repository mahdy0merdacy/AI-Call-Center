import { motion } from 'framer-motion';
import { Check, X } from 'lucide-react';
import { messages } from '@/i18n';
import { passwordRules, passwordStrength } from '@/lib/passwordPolicy';
import { cn } from '@/lib/utils';

const p = messages.passwordPolicy;

const segmentColors = ['bg-destructive', 'bg-destructive', 'bg-warning', 'bg-info', 'bg-success'];
const labelColors = ['text-muted-foreground', 'text-destructive', 'text-warning', 'text-info', 'text-success'];

interface PasswordStrengthProps {
  password: string;
  email?: string;
  id?: string;
  className?: string;
}

/** Animated 4-segment strength meter + live policy checklist. */
export function PasswordStrength({ password, email = '', id, className }: PasswordStrengthProps) {
  const score = passwordStrength(password, email);
  const level = p.levels[score] ?? '';

  return (
    <div id={id} className={cn('space-y-3', className)}>
      <div>
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="text-muted-foreground">{p.strength}</span>
          {/* Announced politely as it changes. */}
          <span aria-live="polite" className={cn('font-semibold', password ? labelColors[score] : 'text-muted-foreground')}>
            {password ? level : '—'}
          </span>
        </div>
        <div className="grid grid-cols-4 gap-1.5" aria-hidden>
          {[1, 2, 3, 4].map((segment) => (
            <div key={segment} className="h-1.5 overflow-hidden rounded-full bg-muted-foreground/15">
              <motion.div
                className={cn('h-full origin-left rounded-full', segmentColors[score])}
                initial={false}
                animate={{ scaleX: password && score >= segment ? 1 : password && segment === 1 ? 0.35 : 0 }}
                transition={{ type: 'spring', stiffness: 260, damping: 30, delay: segment * 0.03 }}
              />
            </div>
          ))}
        </div>
      </div>

      <div>
        <p className="mb-1.5 text-xs font-medium text-muted-foreground">{p.title}</p>
        <ul className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
          {passwordRules.map((rule) => {
            const ok = rule.test(password, email);
            return (
              <li key={rule.id} className="flex items-center gap-2 text-[13px]">
                <motion.span
                  initial={false}
                  animate={{ scale: ok ? [1, 1.25, 1] : 1 }}
                  transition={{ duration: 0.25 }}
                  className={cn(
                    'flex size-4 shrink-0 items-center justify-center rounded-full transition-colors',
                    ok ? 'bg-success text-success-foreground' : 'bg-muted-foreground/15 text-muted-foreground',
                  )}
                >
                  {ok ? <Check className="size-3" strokeWidth={3} aria-hidden /> : <X className="size-2.5" strokeWidth={3} aria-hidden />}
                </motion.span>
                <span className={cn('transition-colors', ok ? 'text-foreground' : 'text-muted-foreground')}>
                  {rule.label}
                  <span className="sr-only">: {ok ? p.met : p.notMet}</span>
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
