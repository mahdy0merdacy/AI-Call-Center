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
  // A signal-red tile carrying a spoken waveform; the last bar is the reply.
  return (
    <svg viewBox="0 0 32 32" className={cn('size-8', className)} aria-hidden>
      <rect width="32" height="32" rx="8" fill="hsl(var(--brand-signal))" />
      <g fill="hsl(var(--brand-ink))">
        <rect x="6.5" y="13" width="3" height="6" rx="1.5" />
        <rect x="11.5" y="8.5" width="3" height="15" rx="1.5" />
        <rect x="16.5" y="5.5" width="3" height="21" rx="1.5" />
      </g>
      <rect x="21.5" y="11" width="3" height="10" rx="1.5" fill="hsl(40 40% 97%)" />
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
              'font-serif text-[20px] tracking-[-0.01em]',
              inverted ? 'text-brand-cream' : 'text-foreground',
            )}
          >
            {env.appName}
          </span>
          <span
            className={cn(
              'mt-0.5 font-mono text-[9.5px] font-medium uppercase tracking-[0.18em]',
              inverted ? 'text-brand-cream/55' : 'text-muted-foreground',
            )}
          >
            {messages.brand.tagline}
          </span>
        </span>
      )}
    </span>
  );
}
