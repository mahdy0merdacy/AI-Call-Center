import { forwardRef, type InputHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

const Input = forwardRef<HTMLInputElement, InputProps>(({ className, type, ...props }, ref) => (
  <input
    ref={ref}
    type={type ?? 'text'}
    className={cn(
      'flex h-11 w-full rounded-lg border border-input bg-background px-3.5 text-[15px] text-foreground shadow-sm sm:text-sm',
      'transition-[border-color,box-shadow,background-color] duration-200 ease-out',
      'placeholder:text-muted-foreground/70',
      'hover:border-foreground/25',
      'focus-visible:border-primary focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/20',
      'aria-[invalid=true]:border-destructive aria-[invalid=true]:focus-visible:ring-destructive/20',
      'disabled:cursor-not-allowed disabled:opacity-60',
      'read-only:bg-muted/60 read-only:focus-visible:ring-0',
      'file:border-0 file:bg-transparent file:text-sm file:font-medium',
      className,
    )}
    {...props}
  />
));
Input.displayName = 'Input';

export { Input };
