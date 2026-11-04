import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import { motion } from 'framer-motion';
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react';
import { cn } from '@/lib/utils';

const Checkbox = forwardRef<
  ElementRef<typeof CheckboxPrimitive.Root>,
  ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
>(({ className, ...props }, ref) => (
  <CheckboxPrimitive.Root
    ref={ref}
    className={cn(
      'peer size-5 shrink-0 rounded-md border border-input bg-background shadow-sm transition-colors',
      'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/25',
      'data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground',
      'aria-[invalid=true]:border-destructive disabled:cursor-not-allowed disabled:opacity-50',
      className,
    )}
    {...props}
  >
    <CheckboxPrimitive.Indicator forceMount className="flex items-center justify-center data-[state=unchecked]:invisible">
      <svg viewBox="0 0 16 16" className="size-3.5" aria-hidden>
        <motion.path
          d="M3.5 8.5l3 3 6-7"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.2}
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={false}
          animate={{ pathLength: props.checked === true ? 1 : 0 }}
          transition={{ duration: 0.2 }}
        />
      </svg>
    </CheckboxPrimitive.Indicator>
  </CheckboxPrimitive.Root>
));
Checkbox.displayName = CheckboxPrimitive.Root.displayName;

export { Checkbox };
