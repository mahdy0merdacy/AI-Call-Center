import { motion } from 'framer-motion';
import { LogoMark } from '@/components/brand/Logo';
import { WaveformVisual } from '@/components/ui/WaveformVisual';
import { messages } from '@/i18n';
import { env } from '@/lib/env';

/** Branded full-screen loader shown while the session is restored on boot. */
export function SplashScreen({ label = messages.splash.restoring }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="console-bg grain relative flex min-h-dvh flex-col items-center justify-center overflow-hidden text-brand-cream"
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4 }}
        className="flex w-full max-w-xs flex-col items-center gap-6 px-6"
      >
        <LogoMark className="size-14 drop-shadow-[0_12px_30px_hsl(var(--brand-signal)/0.45)]" />
        <div className="h-10 w-full" aria-hidden>
          <WaveformVisual voice="thinking" bars={28} />
        </div>
        <div className="text-center">
          <p className="font-serif text-2xl leading-none">{env.appName}</p>
          <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.16em] text-brand-cream/60">{label}</p>
        </div>
      </motion.div>
    </div>
  );
}
