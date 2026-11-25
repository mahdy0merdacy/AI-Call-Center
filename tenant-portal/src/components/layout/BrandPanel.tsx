import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { CalendarCheck2, PhoneIncoming, Sparkles, type LucideIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Logo } from '@/components/brand/Logo';
import { LocaleToggle } from '@/components/LocaleToggle';
import { ThemeToggle } from '@/components/ThemeToggle';
import { WaveformVisual } from '@/components/ui/WaveformVisual';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';

const floatingIcons: LucideIcon[] = [PhoneIncoming, CalendarCheck2, Sparkles];

const floatingPositions = [
  'left-[4%] top-[2%]',
  'right-[2%] top-[38%]',
  'left-[10%] bottom-[2%]',
];

function FloatingCard({ index }: { index: number }) {
  const reduceMotion = useReducedMotion();
  const item = messages.brand.floating[index];
  const Icon = floatingIcons[index];
  if (!item || !Icon) return null;

  return (
    <motion.div
      className={cn('glass absolute z-10 flex items-center gap-3 rounded-xl px-3.5 py-3', floatingPositions[index])}
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={
        reduceMotion
          ? { opacity: 1, y: 0, scale: 1 }
          : { opacity: 1, y: [0, -8, 0], scale: 1 }
      }
      transition={{
        opacity: { duration: 0.6, delay: 0.3 + index * 0.18 },
        scale: { duration: 0.6, delay: 0.3 + index * 0.18 },
        y: reduceMotion
          ? { duration: 0.6 }
          : { duration: 6 + index, repeat: Infinity, ease: 'easeInOut', delay: index * 0.8 },
      }}
    >
      <span className="flex size-9 items-center justify-center rounded-lg bg-white/10 text-white">
        <Icon className="size-[18px]" aria-hidden />
      </span>
      <span className="pr-1">
        <span className="block text-[13px] font-semibold text-white">{item.title}</span>
        <span className="block text-xs text-white/65">{item.body}</span>
      </span>
    </motion.div>
  );
}

function LiveTranscript() {
  const lines = messages.brand.transcript;
  const [index, setIndex] = useState(0);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (reduceMotion) return;
    const id = window.setInterval(() => setIndex((value) => (value + 1) % lines.length), 3600);
    return () => window.clearInterval(id);
  }, [lines.length, reduceMotion]);

  return (
    <div className="glass relative overflow-hidden rounded-2xl px-5 py-4">
      <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-white/60">
        <span className="relative flex size-2">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-brand-teal opacity-60" />
          <span className="relative inline-flex size-2 rounded-full bg-brand-teal" />
        </span>
        {messages.brand.liveCall}
      </div>
      <div className="relative h-11">
        <AnimatePresence mode="wait">
          <motion.p
            key={index}
            initial={{ opacity: 0, y: 10, filter: 'blur(4px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -10, filter: 'blur(4px)' }}
            transition={{ duration: 0.45 }}
            className="absolute inset-0 text-[15px] leading-snug text-white/90"
          >
            {lines[index]}
          </motion.p>
        </AnimatePresence>
      </div>
    </div>
  );
}

/** Right-hand marketing panel of the auth split layout (desktop only). */
export function BrandPanel({ className }: { className?: string }) {
  return (
    <aside
      aria-hidden
      className={cn(
        'mesh-bg grain relative isolate flex-col overflow-hidden p-10 text-white xl:p-14',
        className,
      )}
    >
      {/* Drifting colour blobs */}
      <div className="pointer-events-none absolute -left-24 -top-24 -z-10 size-[28rem] animate-drift rounded-full bg-brand-indigo/40 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 right-[-6rem] -z-10 size-[30rem] animate-drift-slow rounded-full bg-brand-teal/20 blur-3xl" />
      <div className="pointer-events-none absolute right-1/4 top-1/3 -z-10 size-72 animate-drift rounded-full bg-brand-violet/30 blur-3xl [animation-delay:-6s]" />

      <Logo inverted />

      <div className="relative flex flex-1 items-center">
        <div className="relative h-[340px] w-full">
          <div className="absolute inset-x-0 top-1/2 h-40 -translate-y-1/2 opacity-95">
            <WaveformVisual bars={64} />
          </div>
          {messages.brand.floating.map((_, index) => (
            <FloatingCard key={index} index={index} />
          ))}
        </div>
      </div>

      <div className="relative max-w-lg space-y-6">
        <motion.h2
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="text-4xl font-bold leading-[1.1] xl:text-[2.75rem]"
        >
          {messages.brand.headline}
          <br />
          <span className="text-gradient">{messages.brand.headlineAccent}</span>
        </motion.h2>
        <p className="text-[15px] leading-relaxed text-white/70">{messages.brand.description}</p>
        <LiveTranscript />
      </div>
    </aside>
  );
}

const onDarkControl = 'border-white/20 bg-white/10 text-white hover:bg-white/20 hover:text-white';

/** Compact gradient header replacing the brand panel on small screens. */
export function MobileBrandHeader({ className }: { className?: string }) {
  return (
    <div className={cn('mesh-bg relative overflow-hidden px-5 pb-5 pt-5 text-white', className)}>
      <div className="flex items-center justify-between">
        <Logo inverted />
        <div className="flex items-center gap-2">
          <LocaleToggle className={onDarkControl} />
          <ThemeToggle className={onDarkControl} />
        </div>
      </div>
      <div className="mt-4 h-10 opacity-90">
        <WaveformVisual bars={36} intensity={0.85} />
      </div>
    </div>
  );
}
