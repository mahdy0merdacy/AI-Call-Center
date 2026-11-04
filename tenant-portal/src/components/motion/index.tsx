import { motion, useAnimationControls, type HTMLMotionProps, type Variants } from 'framer-motion';
import { useCallback, type ReactNode } from 'react';

export const easeOutExpo = [0.22, 1, 0.36, 1] as const;

export const staggerContainer: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.04 } },
};

export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: easeOutExpo } },
};

type DivMotionProps = Omit<HTMLMotionProps<'div'>, 'variants' | 'initial' | 'animate'>;

/** Children wrapped in <StaggerItem> enter one after the other. */
export function Stagger({ children, ...props }: DivMotionProps & { children: ReactNode }) {
  return (
    <motion.div variants={staggerContainer} initial="hidden" animate="show" {...props}>
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, ...props }: DivMotionProps & { children: ReactNode }) {
  return (
    <motion.div variants={staggerItem} {...props}>
      {children}
    </motion.div>
  );
}

/** Fade + slight slide used for every route change. */
export function PageTransition({ children, ...props }: DivMotionProps & { children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={{ duration: 0.28, ease: easeOutExpo }}
      {...props}
    >
      {children}
    </motion.div>
  );
}

/**
 * Horizontal shake for failed submits. Spread `controls` on a motion element
 * as `animate={controls}` and call `shake()`.
 */
export function useShake() {
  const controls = useAnimationControls();
  const shake = useCallback(() => {
    void controls.start({
      x: [0, -10, 10, -7, 7, -3, 3, 0],
      transition: { duration: 0.45, ease: 'easeInOut' },
    });
  }, [controls]);
  return { controls, shake };
}
