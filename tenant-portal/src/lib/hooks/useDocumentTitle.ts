import { useEffect } from 'react';
import { messages } from '@/i18n';
import { env } from '@/lib/env';

/**
 * "Sign in · AI Call Center". Each route sets its own title so screen-reader
 * users and browser history/tabs can tell pages apart.
 */
export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} · ${env.appName}` : `${messages.brand.tagline} · ${env.appName}`;
  }, [title]);
}
