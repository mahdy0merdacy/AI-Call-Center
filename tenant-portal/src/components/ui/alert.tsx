import { cva, type VariantProps } from 'class-variance-authority';
import { motion } from 'framer-motion';
import { AlertTriangle, CheckCircle2, Info, XCircle, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

const alertVariants = cva(
  'relative flex w-full gap-3 rounded-lg border px-4 py-3 text-sm [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0',
  {
    variants: {
      variant: {
        info: 'border-info/25 bg-info/[0.07] text-foreground [&>svg]:text-info',
        success: 'border-success/30 bg-success/[0.08] text-foreground [&>svg]:text-success',
        warning: 'border-warning/30 bg-warning/[0.08] text-foreground [&>svg]:text-warning',
        destructive:
          'border-destructive/30 bg-destructive/[0.07] text-foreground [&>svg]:text-destructive',
      },
    },
    defaultVariants: { variant: 'info' },
  },
);

const icons: Record<NonNullable<VariantProps<typeof alertVariants>['variant']>, LucideIcon> = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  destructive: XCircle,
};

export interface AlertProps extends VariantProps<typeof alertVariants> {
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
  /** Extra content rendered on the right (e.g. a countdown ring or a button). */
  aside?: ReactNode;
  /**
   * `assertive` for errors the user must notice right away, `polite` for
   * confirmations. Defaults based on the variant.
   */
  live?: 'polite' | 'assertive' | 'off';
}

export function Alert({ variant, title, children, className, aside, live }: AlertProps) {
  const resolvedVariant = variant ?? 'info';
  const Icon = icons[resolvedVariant];
  const politeness = live ?? (resolvedVariant === 'destructive' ? 'assertive' : 'polite');

  return (
    <motion.div
      role={politeness === 'assertive' ? 'alert' : 'status'}
      aria-live={politeness}
      initial={{ opacity: 0, y: -6, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -6, scale: 0.98 }}
      transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
      className={cn(alertVariants({ variant: resolvedVariant }), className)}
    >
      <Icon aria-hidden />
      <div className="min-w-0 flex-1 space-y-1">
        {title && <p className="font-semibold leading-snug">{title}</p>}
        {children && <div className="leading-relaxed text-muted-foreground">{children}</div>}
      </div>
      {aside && <div className="shrink-0 self-center">{aside}</div>}
    </motion.div>
  );
}
