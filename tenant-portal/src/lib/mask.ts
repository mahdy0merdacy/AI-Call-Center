/** "amira.haddad@acme.test" → "a••••••••d@acme.test" (shown in confirmations, never logged). */
export function maskEmail(email: string): string {
  const [local = '', domain = ''] = email.split('@');
  if (!domain) return email;
  if (local.length <= 2) return `${local[0] ?? ''}•@${domain}`;
  const hidden = '•'.repeat(Math.min(8, local.length - 2));
  return `${local[0]}${hidden}${local[local.length - 1]}@${domain}`;
}

interface MailProvider {
  name: string;
  url: string;
}

const providers: { domains: string[]; provider: MailProvider }[] = [
  { domains: ['gmail.com', 'googlemail.com'], provider: { name: 'Gmail', url: 'https://mail.google.com/' } },
  {
    domains: ['outlook.com', 'hotmail.com', 'hotmail.fr', 'live.com', 'live.fr', 'msn.com'],
    provider: { name: 'Outlook', url: 'https://outlook.live.com/mail/' },
  },
  { domains: ['yahoo.com', 'yahoo.fr'], provider: { name: 'Yahoo Mail', url: 'https://mail.yahoo.com/' } },
  { domains: ['icloud.com', 'me.com'], provider: { name: 'iCloud Mail', url: 'https://www.icloud.com/mail' } },
  { domains: ['proton.me', 'protonmail.com'], provider: { name: 'Proton Mail', url: 'https://mail.proton.me/' } },
  { domains: ['orange.fr', 'wanadoo.fr'], provider: { name: 'Orange Mail', url: 'https://messagerie.orange.fr/' } },
];

/** Webmail shortcut for well-known consumer domains; null for company domains. */
export function mailProviderFor(email: string): MailProvider | null {
  const domain = email.split('@')[1]?.toLowerCase();
  if (!domain) return null;
  return providers.find((entry) => entry.domains.includes(domain))?.provider ?? null;
}
