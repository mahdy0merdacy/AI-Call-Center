import { locale } from '@/i18n';

const LANGUAGE_TAG = locale === 'fr' ? 'fr-FR' : 'en-GB';

/** The documents service and the upload limit count in binary units (25 MB = 25 * 1024 * 1024). */
const UNITS = locale === 'fr' ? ['o', 'Ko', 'Mo', 'Go'] : ['B', 'KB', 'MB', 'GB'];
const number = new Intl.NumberFormat(LANGUAGE_TAG, { maximumFractionDigits: 1 });

export function formatFileSize(bytes: number): string {
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // Bytes are whole numbers; larger units get one decimal at most.
  return `${unit === 0 ? Math.round(value) : number.format(value)} ${UNITS[unit]}`;
}

const dateTime = new Intl.DateTimeFormat(LANGUAGE_TAG, { dateStyle: 'medium', timeStyle: 'short' });

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : dateTime.format(date);
}

/** "report.final.PDF" -> "PDF". Empty when the name has no extension. */
export function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot + 1).toUpperCase() : '';
}
