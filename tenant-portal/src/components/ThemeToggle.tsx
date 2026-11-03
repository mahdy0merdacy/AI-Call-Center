import { AnimatePresence, motion } from 'framer-motion';
import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react';
import { useTheme, type ThemePreference } from '@/app/providers/ThemeProvider';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';

const order: ThemePreference[] = ['system', 'light', 'dark'];

const meta: Record<ThemePreference, { icon: LucideIcon; label: string }> = {
  system: { icon: Monitor, label: messages.theme.system },
  light: { icon: Sun, label: messages.theme.light },
  dark: { icon: Moon, label: messages.theme.dark },
};

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const index = order.indexOf(theme);
  const next = order[(index + 1) % order.length] ?? 'system';
  const { icon: Icon, label } = meta[theme];

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={messages.theme.switchTo(label)}
      title={label}
      className={cn(
        'relative inline-flex size-9 items-center justify-center overflow-hidden rounded-lg border border-border/70 bg-background/60 text-muted-foreground backdrop-blur',
        'transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/30',
        className,
      )}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={theme}
          initial={{ y: 12, opacity: 0, rotate: -45 }}
          animate={{ y: 0, opacity: 1, rotate: 0 }}
          exit={{ y: -12, opacity: 0, rotate: 45 }}
          transition={{ duration: 0.2 }}
          className="inline-flex"
        >
          <Icon className="size-4" aria-hidden />
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
