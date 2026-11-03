import { AnimatePresence, motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

export interface FieldControlProps {
  id: string;
  'aria-invalid': boolean;
  'aria-describedby': string | undefined;
}

interface FieldProps {
  id: string;
  label: ReactNode;
  error?: string;
  hint?: ReactNode;
  /** Rendered on the label row, right-aligned (e.g. "Forgot password?"). */
  labelAside?: ReactNode;
  className?: string;
  /** Receives the aria wiring to spread on the control. */
  children: (control: FieldControlProps) => ReactNode;
}

/**
 * Label + control + hint + animated error, with the ARIA links between them.
 * The label tints to the primary colour while the control has focus.
 */
export function Field({ id, label, error, hint, labelAside, className, children }: FieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [errorId, hintId].filter(Boolean).join(' ') || undefined;

  return (
    <div className={cn('group space-y-2', className)}>
      <div className="flex items-center justify-between gap-3">
        <Label
          htmlFor={id}
          className={cn(
            'transition-colors duration-200 group-focus-within:text-primary',
            error && 'text-destructive group-focus-within:text-destructive',
          )}
        >
          {label}
        </Label>
        {labelAside}
      </div>
      {children({ id, 'aria-invalid': Boolean(error), 'aria-describedby': describedBy })}
      {hint && !error && (
        <p id={hintId} className="text-[13px] text-muted-foreground">
          {hint}
        </p>
      )}
      <AnimatePresence initial={false}>
        {error && (
          <motion.p
            key="error"
            id={errorId}
            role="alert"
            initial={{ opacity: 0, height: 0, y: -4 }}
            animate={{ opacity: 1, height: 'auto', y: 0 }}
            exit={{ opacity: 0, height: 0, y: -4 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden text-[13px] font-medium text-destructive"
          >
            {error}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}

/** "or" separator between social and email sign-in. */
export function Divider({ children }: { children: ReactNode }) {
  return (
    <div className="relative my-6 flex items-center" role="separator">
      <span className="h-px flex-1 bg-border" />
      <span className="px-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {children}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
