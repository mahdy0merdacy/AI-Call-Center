import { messages } from '@/i18n';
import { formatClock, useCountdown } from '@/lib/hooks/useCountdown';
import { cn } from '@/lib/utils';

interface CountdownRingProps {
  /** Epoch ms when the countdown ends. */
  endsAt: number;
  /** Full duration, so the ring shows the fraction left. */
  totalSeconds: number;
  onComplete?: () => void;
  size?: number;
  tone?: 'primary' | 'warning' | 'destructive';
  /** false = ring only (for small inline use next to a text label). */
  showLabel?: boolean;
  className?: string;
}

const toneClass = {
  primary: 'text-primary',
  warning: 'text-warning',
  destructive: 'text-destructive',
} as const;

/** Circular countdown for lockouts, throttling, MFA challenges and resend cooldowns. */
export function CountdownRing({
  endsAt,
  totalSeconds,
  onComplete,
  size = 56,
  tone = 'primary',
  showLabel = true,
  className,
}: CountdownRingProps) {
  const secondsLeft = useCountdown(endsAt, onComplete);
  const stroke = size < 32 ? 2.5 : 4;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const fraction = totalSeconds > 0 ? Math.min(1, secondsLeft / totalSeconds) : 0;
  const clock = formatClock(secondsLeft);
  // Long timers (1 h lockout) need smaller digits to fit.
  const fontSize = clock.length > 5 ? size * 0.2 : size * 0.26;

  return (
    <div
      role="timer"
      aria-label={messages.countdown.remaining(clock)}
      className={cn('relative inline-flex shrink-0', toneClass[tone], className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeOpacity={0.15}
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - fraction)}
          className="transition-[stroke-dashoffset] duration-300 ease-linear"
        />
      </svg>
      {showLabel && (
        <span
          aria-hidden
          className="absolute inset-0 flex items-center justify-center font-mono font-semibold tabular-nums text-foreground"
          style={{ fontSize }}
        >
          {clock}
        </span>
      )}
    </div>
  );
}
