import { useId } from 'react';
import { messages } from '@/i18n';
import { env } from '@/lib/env';
import { cn } from '@/lib/utils';

interface LogoProps {
  className?: string;
  /** Light text for dark backgrounds (brand panel, mobile header). */
  inverted?: boolean;
  showWordmark?: boolean;
}

export function LogoMark({ className }: { className?: string }) {
  // React ids contain ':' which breaks `url(#…)` references in some browsers.
  const gradientId = `logo-${useId().replace(/:/g, '')}`;
  return (
    <svg viewBox="0 0 32 32" className={cn('size-8', className)} aria-hidden>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="hsl(var(--brand-indigo))" />
          <stop offset="1" stopColor="hsl(var(--brand-violet))" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${gradientId})`} />
      <g fill="#fff">
        <rect x="7" y="13" width="3" height="6" rx="1.5" />
        <rect x="12" y="9" width="3" height="14" rx="1.5" />
        <rect x="17" y="6" width="3" height="20" rx="1.5" />
        <rect x="22" y="11" width="3" height="10" rx="1.5" fill="hsl(var(--brand-teal))" />
      </g>
    </svg>
  );
}

export function Logo({ className, inverted = false, showWordmark = true }: LogoProps) {
  return (
    <span className={cn('inline-flex items-center gap-2.5', className)}>
      <LogoMark />
      {showWordmark && (
        <span className="flex flex-col leading-none">
          <span
            className={cn(
              'text-[15px] font-bold tracking-tight',
              inverted ? 'text-white' : 'text-foreground',
            )}
          >
            {env.appName}
          </span>
          <span
            className={cn(
              'mt-1 text-[11px] font-medium uppercase tracking-[0.12em]',
              inverted ? 'text-white/60' : 'text-muted-foreground',
            )}
          >
            {messages.brand.tagline}
          </span>
        </span>
      )}
    </span>
  );
}
