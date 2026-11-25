import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

/** Success checkmark: the circle and then the tick draw themselves. */
export function AnimatedCheck({ className, size = 72 }: { className?: string; size?: number }) {
  return (
    <motion.div
      className={cn('relative inline-flex text-success', className)}
      style={{ width: size, height: size }}
      initial={{ scale: 0.6, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: 'spring', stiffness: 260, damping: 18 }}
      aria-hidden
    >
      <motion.span
        className="absolute inset-0 rounded-full bg-success/15"
        initial={{ scale: 0.4, opacity: 0 }}
        animate={{ scale: [0.4, 1.25, 1], opacity: [0, 0.8, 1] }}
        transition={{ duration: 0.7, ease: 'easeOut' }}
      />
      <svg viewBox="0 0 52 52" className="relative size-full">
        <motion.circle
          cx="26"
          cy="26"
          r="23"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.5, ease: 'easeInOut' }}
        />
        <motion.path
          d="M15 27l7.5 7.5L37.5 19"
          fill="none"
          stroke="currentColor"
          strokeWidth="4"
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.35, delay: 0.45, ease: 'easeOut' }}
        />
      </svg>
    </motion.div>
  );
}
