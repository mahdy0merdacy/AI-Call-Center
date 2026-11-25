import { en, type Messages } from './en';
import { fr } from './fr';

export type Locale = 'en' | 'fr';

const dictionaries: Record<Locale, Messages> = { en, fr };
const STORAGE_KEY = 'tp-locale';

/**
 * Stored choice, else the browser language. Resolved once at boot: changing
 * language calls `setLocale`, which reloads so module-level copy stays simple.
 */
function detectLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'en' || stored === 'fr') return stored;
  } catch {
    // Storage unavailable: fall through to the browser language.
  }
  return navigator.language.toLowerCase().startsWith('fr') ? 'fr' : 'en';
}

export const locale: Locale = detectLocale();
export const messages: Messages = dictionaries[locale];

document.documentElement.lang = locale;

export function setLocale(next: Locale): void {
  if (next === locale) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Can't persist, so a reload would come back in the same language.
    return;
  }
  window.location.reload();
}

export type { Messages };
