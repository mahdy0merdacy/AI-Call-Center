import { motion } from 'framer-motion';
import { Logo } from '@/components/brand/Logo';
import { LocaleToggle } from '@/components/LocaleToggle';
import { ThemeToggle } from '@/components/ThemeToggle';
import { CallConsole, CallTicker } from '@/components/voice/CallConsole';
import { demoCalls } from '@/components/voice/demoCalls';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';

/**
 * Right-hand panel of the auth split layout (desktop only): the pitch, then a
 * sample call replaying live on the console.
 */
export function BrandPanel({ className }: { className?: string }) {
  return (
    <aside
      aria-hidden
      className={cn(
        'console-bg grain relative isolate flex-col overflow-hidden p-8 text-brand-cream xl:p-12',
        className,
      )}
    >
      <Logo inverted />

      <div className="mt-8 max-w-xl xl:mt-12">
        <motion.h2
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
          className="font-serif text-[2.6rem] font-normal leading-[0.98] tracking-[-0.01em] xl:text-[3.4rem] 2xl:text-[4rem]"
        >
          {messages.brand.headline}
          <br />
          <em className="text-brand-signal">{messages.brand.headlineAccent}</em>
        </motion.h2>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.7, delay: 0.3 }}
          className="mt-5 max-w-md text-[15px] leading-relaxed text-brand-cream/65 [@media(max-height:820px)]:hidden"
        >
          {messages.brand.description}
        </motion.p>
      </div>

      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8, delay: 0.35, ease: [0.22, 1, 0.36, 1] }}
        className="mt-8 flex min-h-0 flex-1 flex-col"
      >
        <CallConsole
          calls={demoCalls}
          className="min-h-0 flex-1"
          transcriptClassName="h-auto min-h-[150px] flex-1"
        />
      </motion.div>

      <dl className="mt-6 grid grid-cols-3 gap-4 border-t border-brand-cream/10 pt-5 [@media(max-height:720px)]:hidden">
        {messages.brand.proof.map((item) => (
          <div key={item.value} className="min-w-0">
            <dt className="font-serif text-2xl leading-none text-brand-cream xl:text-[1.7rem]">{item.value}</dt>
            <dd className="mt-1.5 text-xs leading-snug text-brand-cream/50">{item.label}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

const onDarkControl =
  'border-brand-cream/20 bg-brand-cream/10 text-brand-cream hover:bg-brand-cream/20 hover:text-brand-cream';

/** Compact console header replacing the brand panel on small screens. */
export function MobileBrandHeader({ className }: { className?: string }) {
  return (
    <div className={cn('console-bg relative overflow-hidden px-5 pb-4 pt-5 text-brand-cream', className)}>
      <div className="flex items-center justify-between">
        <Logo inverted />
        <div className="flex items-center gap-2">
          <LocaleToggle className={onDarkControl} />
          <ThemeToggle className={onDarkControl} />
        </div>
      </div>
      <CallTicker calls={demoCalls} className="mt-4" />
    </div>
  );
}
