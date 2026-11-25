import { motion, useReducedMotion } from 'framer-motion';
import { useId } from 'react';
import { cn } from '@/lib/utils';

/** Envelope whose flap opens as a letter rises out of it, then gently floats. */
export function EnvelopeIllustration({ className }: { className?: string }) {
  const reduceMotion = useReducedMotion();
  const gradientId = `env-${useId().replace(/:/g, '')}`;
  const fill = `url(#${gradientId})`;

  return (
    <motion.svg
      viewBox="0 0 120 100"
      className={cn('h-24 w-28', className)}
      aria-hidden
      animate={reduceMotion ? undefined : { y: [0, -4, 0] }}
      transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut', delay: 1.2 }}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="hsl(var(--brand-indigo))" />
          <stop offset="1" stopColor="hsl(var(--brand-violet))" />
        </linearGradient>
      </defs>
      {/* back of the envelope */}
      <rect x="14" y="38" width="92" height="56" rx="8" fill={fill} opacity="0.35" />
      {/* letter */}
      <motion.g
        initial={{ y: 22 }}
        animate={{ y: 0 }}
        transition={{ delay: 0.45, duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      >
        <rect x="26" y="18" width="68" height="54" rx="6" className="fill-card" stroke="hsl(var(--border))" />
        <rect x="35" y="29" width="34" height="5" rx="2.5" fill="hsl(var(--brand-indigo))" opacity="0.7" />
        <rect x="35" y="40" width="50" height="4" rx="2" fill="hsl(var(--muted-foreground))" opacity="0.3" />
        <rect x="35" y="49" width="42" height="4" rx="2" fill="hsl(var(--muted-foreground))" opacity="0.3" />
        <circle cx="82" cy="31" r="5" fill="hsl(var(--brand-teal))" />
      </motion.g>
      {/* front pocket */}
      <path d="M14 46 L60 74 L106 46 V86 a8 8 0 0 1 -8 8 H22 a8 8 0 0 1 -8 -8 Z" fill={fill} />
      {/* flap, hinged on its bottom edge (originY: 1 = y 46) */}
      <motion.path
        d="M14 46 L60 14 L106 46"
        fill={fill}
        opacity={0.9}
        style={{ originY: 1 }}
        initial={{ scaleY: -1 }}
        animate={{ scaleY: 1 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
      />
    </motion.svg>
  );
}
