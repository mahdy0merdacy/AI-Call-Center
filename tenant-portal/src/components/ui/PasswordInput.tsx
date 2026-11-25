import { AnimatePresence, motion } from 'framer-motion';
import { Eye, EyeOff } from 'lucide-react';
import { forwardRef, useState, type KeyboardEvent } from 'react';
import { Input, type InputProps } from '@/components/ui/input';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';

export type PasswordInputProps = Omit<InputProps, 'type'>;

/** Password field with a show/hide toggle and a Caps Lock warning. */
export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className, onKeyUp, onBlur, ...props }, ref) => {
    const [visible, setVisible] = useState(false);
    const [capsLock, setCapsLock] = useState(false);

    const handleKey = (event: KeyboardEvent<HTMLInputElement>) => {
      setCapsLock(event.getModifierState('CapsLock'));
      onKeyUp?.(event);
    };

    return (
      <div>
        <div className="relative">
          <Input
            ref={ref}
            type={visible ? 'text' : 'password'}
            className={cn('pr-11', className)}
            onKeyUp={handleKey}
            onBlur={(event) => {
              setCapsLock(false);
              onBlur?.(event);
            }}
            {...props}
          />
          <button
            type="button"
            onClick={() => setVisible((value) => !value)}
            aria-label={visible ? messages.fields.hidePassword : messages.fields.showPassword}
            aria-pressed={visible}
            aria-controls={props.id}
            className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-lg text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
          >
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={visible ? 'hide' : 'show'}
                initial={{ opacity: 0, scale: 0.6 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.6 }}
                transition={{ duration: 0.15 }}
                className="inline-flex"
              >
                {visible ? (
                  <EyeOff className="size-[18px]" aria-hidden />
                ) : (
                  <Eye className="size-[18px]" aria-hidden />
                )}
              </motion.span>
            </AnimatePresence>
          </button>
        </div>
        {capsLock && (
          <p className="mt-1.5 text-[13px] font-medium text-warning" role="status">
            {messages.fields.capsLock}
          </p>
        )}
      </div>
    );
  },
);
PasswordInput.displayName = 'PasswordInput';
