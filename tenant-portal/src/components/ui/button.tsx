import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { AnimatePresence, motion } from 'framer-motion';
import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { Spinner } from '@/components/ui/spinner';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  [
    'relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold',
    'transition-[background-color,border-color,color,box-shadow,transform] duration-200',
    'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/30 focus-visible:ring-offset-0',
    'disabled:pointer-events-none disabled:opacity-60 active:scale-[0.985]',
    '[&_svg]:pointer-events-none [&_svg]:shrink-0',
  ],
  {
    variants: {
      variant: {
        default:
          'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90 hover:shadow-glow',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
        outline:
          'border border-input bg-background text-foreground shadow-sm hover:bg-muted hover:border-foreground/20',
        ghost: 'text-foreground hover:bg-muted',
        destructive:
          'bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90',
        success: 'bg-success text-success-foreground shadow-sm hover:bg-success/90',
        link: 'h-auto p-0 font-semibold text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-11 px-5',
        sm: 'h-9 px-3.5 text-[13px]',
        lg: 'h-12 px-6 text-[15px]',
        icon: 'size-10',
      },
      block: {
        true: 'w-full',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  /** Shows a spinner, keeps the button's width and disables it. */
  loading?: boolean;
  /** Announced to screen readers while loading. */
  loadingText?: string;
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant,
      size,
      block,
      asChild = false,
      loading = false,
      loadingText,
      disabled,
      children,
      type,
      ...props
    },
    ref,
  ) => {
    const classes = cn(buttonVariants({ variant, size, block }), className);

    if (asChild) {
      return (
        <Slot ref={ref} className={classes} {...props}>
          {children}
        </Slot>
      );
    }

    return (
      <button
        ref={ref}
        type={type ?? 'button'}
        className={classes}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        <span
          className={cn(
            'inline-flex items-center gap-2 transition-[opacity,transform] duration-200',
            loading && 'scale-90 opacity-0',
          )}
        >
          {children}
        </span>
        <AnimatePresence>
          {loading && (
            <motion.span
              key="spinner"
              className="absolute inset-0 flex items-center justify-center"
              initial={{ opacity: 0, scale: 0.4, rotate: -90 }}
              animate={{ opacity: 1, scale: 1, rotate: 0 }}
              exit={{ opacity: 0, scale: 0.4 }}
              transition={{ type: 'spring', stiffness: 420, damping: 28 }}
            >
              <Spinner className="size-[18px]" />
              {loadingText && <span className="sr-only">{loadingText}</span>}
            </motion.span>
          )}
        </AnimatePresence>
      </button>
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
