import { Languages } from 'lucide-react';
import { locale, setLocale, type Locale } from '@/i18n';
import { cn } from '@/lib/utils';

const labels: Record<Locale, { short: string; switchTo: string }> = {
  en: { short: 'EN', switchTo: 'Passer en français' },
  fr: { short: 'FR', switchTo: 'Switch to English' },
};

export function LocaleToggle({ className }: { className?: string }) {
  const next: Locale = locale === 'en' ? 'fr' : 'en';
  return (
    <button
      type="button"
      onClick={() => setLocale(next)}
      aria-label={labels[locale].switchTo}
      title={labels[locale].switchTo}
      className={cn(
        'inline-flex h-9 items-center gap-1.5 rounded-lg border border-border/70 bg-background/60 px-2.5 text-xs font-semibold text-muted-foreground backdrop-blur',
        'transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/30',
        className,
      )}
    >
      <Languages className="size-3.5" aria-hidden />
      {labels[locale].short}
    </button>
  );
}
