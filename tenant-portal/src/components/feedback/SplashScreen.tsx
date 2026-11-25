import { motion } from 'framer-motion';
import { LogoMark } from '@/components/brand/Logo';
import { messages } from '@/i18n';
import { env } from '@/lib/env';

const BAR_DELAYS = [0, 0.15, 0.3, 0.45, 0.3, 0.15, 0];

/** Branded full-screen loader shown while the session is restored on boot. */
export function SplashScreen({ label = messages.splash.restoring }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="mesh-bg grain relative flex min-h-dvh flex-col items-center justify-center overflow-hidden text-white"
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.94 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4 }}
        className="flex flex-col items-center gap-6"
      >
        <LogoMark className="size-14 drop-shadow-[0_10px_30px_hsl(var(--brand-indigo)/0.6)]" />
        <div className="flex h-8 items-center gap-1.5" aria-hidden>
          {BAR_DELAYS.map((delay, index) => (
            <span
              key={index}
              className="h-full w-1.5 origin-center animate-bar-bounce rounded-full bg-gradient-to-b from-brand-violet to-brand-teal"
              style={{ animationDelay: `${delay}s` }}
            />
          ))}
        </div>
        <div className="text-center">
          <p className="text-sm font-semibold tracking-tight">{env.appName}</p>
          <p className="mt-1 text-sm text-white/65">{label}</p>
        </div>
      </motion.div>
    </div>
  );
}
