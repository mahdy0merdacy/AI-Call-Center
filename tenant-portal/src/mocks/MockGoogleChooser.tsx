/**
 * Mock mode only: stands in for the Google consent screen. Picking an option
 * plants the allauth session the mock exchange endpoint will read, then does a
 * full page load of the callback, exactly like the real redirect.
 */
import { ChevronRight } from 'lucide-react';
import { paths } from '@/app/paths';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { rememberGoogleReturnTo } from '@/features/auth/api/google';
import { db, persist, type MockGoogleScenario } from './db';

interface Option {
  scenario: MockGoogleScenario | 'cancelled';
  title: string;
  detail: string;
  email: string;
  name: string;
  sub: () => string;
}

const options: Option[] = [
  {
    scenario: 'new',
    title: 'New Google user',
    detail: 'No company yet → onboarding',
    email: 'new.user@gmail.test',
    name: 'Nina Dubois',
    sub: () => `google-sub-${crypto.randomUUID()}`,
  },
  {
    scenario: 'existing',
    title: 'Léa Martin',
    detail: 'Existing Google user, Maison Verte → dashboard',
    email: 'lea.martin@gmail.test',
    name: 'Léa Martin',
    sub: () => 'google-sub-lea-martin',
  },
  {
    scenario: 'conflict',
    title: 'admin@acme.test',
    detail: 'Email already has a password account → email_already_registered',
    email: 'admin@acme.test',
    name: 'Amira Haddad',
    sub: () => `google-sub-${crypto.randomUUID()}`,
  },
  {
    scenario: 'unverified',
    title: 'Unverified Google email',
    detail: 'Google did not verify the address',
    email: 'unverified@gmail.test',
    name: 'Tom Lefèvre',
    sub: () => `google-sub-${crypto.randomUUID()}`,
  },
  {
    scenario: 'expired',
    title: 'Slow sign-in (> 5 min)',
    detail: 'Exchange arrives too late → expired',
    email: 'late@gmail.test',
    name: 'Late User',
    sub: () => `google-sub-${crypto.randomUUID()}`,
  },
  {
    scenario: 'cancelled',
    title: 'Cancel on Google',
    detail: 'User closes the consent screen → ?error=cancelled',
    email: '',
    name: '',
    sub: () => '',
  },
];

interface MockGoogleChooserProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  returnTo?: string;
}

export default function MockGoogleChooser({ open, onOpenChange, returnTo }: MockGoogleChooserProps) {
  const choose = (option: Option) => {
    rememberGoogleReturnTo(returnTo);
    if (option.scenario === 'cancelled') {
      db.googleSession = null;
      persist();
      window.location.assign(`${paths.googleCallback}?error=cancelled`);
      return;
    }
    db.googleSession = {
      scenario: option.scenario,
      sub: option.sub(),
      email: option.email,
      name: option.name,
      createdAt: option.scenario === 'expired' ? Date.now() - 6 * 60 * 1000 : Date.now(),
    };
    persist();
    window.location.assign(paths.googleCallback);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby="mock-google-description">
        <DialogHeader className="text-left">
          <p className="text-xs font-semibold uppercase tracking-wider text-warning">Mock mode</p>
          <DialogTitle>Choose a Google account</DialogTitle>
          <DialogDescription id="mock-google-description">
            Stands in for Google’s consent screen. Each option triggers one scenario of the real flow.
          </DialogDescription>
        </DialogHeader>
        <ul className="mt-5 divide-y rounded-xl border">
          {options.map((option) => (
            <li key={option.scenario}>
              <button
                type="button"
                onClick={() => choose(option)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold">{option.title}</span>
                  <span className="block text-xs text-muted-foreground">{option.detail}</span>
                </span>
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
