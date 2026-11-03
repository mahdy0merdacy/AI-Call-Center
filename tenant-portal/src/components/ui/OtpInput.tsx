import { motion } from 'framer-motion';
import {
  forwardRef,
  useImperativeHandle,
  useRef,
  type ClipboardEvent,
  type KeyboardEvent,
} from 'react';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';

export interface OtpInputHandle {
  focus: () => void;
}

interface OtpInputProps {
  value: string;
  onChange: (value: string) => void;
  /** Called with the full code as soon as every box is filled. */
  onComplete?: (value: string) => void;
  length?: number;
  disabled?: boolean;
  invalid?: boolean;
  autoFocus?: boolean;
  /** id of the element labelling the group. */
  labelledBy?: string;
  describedBy?: string;
}

/**
 * One box per digit. Typing advances, Backspace goes back, arrows move,
 * pasting or SMS/password-manager autofill spreads the digits across boxes.
 */
export const OtpInput = forwardRef<OtpInputHandle, OtpInputProps>(
  (
    {
      value,
      onChange,
      onComplete,
      length = 6,
      disabled = false,
      invalid = false,
      autoFocus = false,
      labelledBy,
      describedBy,
    },
    ref,
  ) => {
    const inputs = useRef<(HTMLInputElement | null)[]>([]);
    const digits = Array.from({ length }, (_, index) => value[index] ?? '');

    const focusAt = (index: number) => {
      const target = inputs.current[Math.max(0, Math.min(length - 1, index))];
      target?.focus();
      target?.select();
    };

    useImperativeHandle(ref, () => ({
      focus: () => focusAt(Math.min(value.length, length - 1)),
    }));

    const commit = (next: string) => {
      const clean = next.replace(/\D/g, '').slice(0, length);
      onChange(clean);
      if (clean.length === length) onComplete?.(clean);
      return clean;
    };

    /** Overwrites digits starting at `index` with `text`, keeping the others. */
    const writeFrom = (index: number, text: string) => {
      const incoming = text.replace(/\D/g, '');
      if (!incoming) return;
      const start = Math.min(index, value.length);
      const next = value.slice(0, start) + incoming + value.slice(start + incoming.length);
      commit(next);
      focusAt(start + incoming.length);
    };

    const handleKeyDown = (index: number, event: KeyboardEvent<HTMLInputElement>) => {
      switch (event.key) {
        case 'Backspace': {
          event.preventDefault();
          if (digits[index]) {
            commit(value.slice(0, index) + value.slice(index + 1));
            focusAt(index);
          } else if (index > 0) {
            commit(value.slice(0, index - 1) + value.slice(index));
            focusAt(index - 1);
          }
          break;
        }
        case 'ArrowLeft':
          event.preventDefault();
          focusAt(index - 1);
          break;
        case 'ArrowRight':
          event.preventDefault();
          focusAt(index + 1);
          break;
        case 'Home':
          event.preventDefault();
          focusAt(0);
          break;
        case 'End':
          event.preventDefault();
          focusAt(length - 1);
          break;
        default:
          break;
      }
    };

    const handlePaste = (index: number, event: ClipboardEvent<HTMLInputElement>) => {
      event.preventDefault();
      writeFrom(index, event.clipboardData.getData('text'));
    };

    return (
      <div
        role="group"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        className="flex justify-between gap-2 sm:gap-2.5"
      >
        {digits.map((digit, index) => (
          <motion.div
            key={index}
            className="relative flex-1"
            animate={digit ? { scale: [1, 1.1, 1] } : { scale: 1 }}
            transition={{ duration: 0.22 }}
          >
            <input
              ref={(element) => {
                inputs.current[index] = element;
              }}
              value={digit}
              onChange={(event) => {
                const raw = event.target.value;
                // Typing over a filled box yields "old+new": keep only the new digit.
                // A longer value is a paste/autofill of the whole code.
                const text = digit && raw.length === 2 ? raw.replace(digit, '') || digit : raw;
                writeFrom(index, text);
              }}
              onKeyDown={(event) => handleKeyDown(index, event)}
              onPaste={(event) => handlePaste(index, event)}
              onFocus={(event) => event.target.select()}
              inputMode="numeric"
              pattern="\d*"
              autoComplete={index === 0 ? 'one-time-code' : 'off'}
              maxLength={length}
              disabled={disabled}
              autoFocus={autoFocus && index === 0}
              aria-label={messages.mfa.digitLabel(index + 1, length)}
              aria-invalid={invalid || undefined}
              className={cn(
                'h-14 w-full rounded-xl border border-input bg-background text-center font-mono text-2xl font-semibold text-foreground shadow-sm caret-primary sm:h-16',
                'transition-[border-color,box-shadow,background-color] duration-200',
                'focus-visible:border-primary focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/20',
                digit && 'border-primary/50 bg-accent/40',
                invalid && 'border-destructive bg-destructive/[0.04] focus-visible:ring-destructive/20',
                'disabled:cursor-not-allowed disabled:opacity-60',
              )}
            />
          </motion.div>
        ))}
      </div>
    );
  },
);
OtpInput.displayName = 'OtpInput';
