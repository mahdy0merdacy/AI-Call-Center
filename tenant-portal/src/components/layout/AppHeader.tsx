import { NavLink } from 'react-router-dom';
import { useAuthedUser } from '@/app/guards';
import { paths } from '@/app/paths';
import { Logo } from '@/components/brand/Logo';
import { LocaleToggle } from '@/components/LocaleToggle';
import { ThemeToggle } from '@/components/ThemeToggle';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';

export function initials(name: string | null, email: string): string {
  const source = name?.trim() || email.split('@')[0] || '?';
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  const letters = parts.length > 1 ? `${parts[0]?.[0] ?? ''}${parts[1]?.[0] ?? ''}` : source.slice(0, 2);
  return letters.toUpperCase();
}

export function Avatar({ name, email, size = 'md' }: { name: string | null; email: string; size?: 'md' | 'lg' }) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-signal to-brand-amber font-bold text-brand-ink shadow-sm',
        size === 'lg' ? 'size-16 text-xl' : 'size-8 text-xs',
      )}
    >
      {initials(name, email)}
    </span>
  );
}

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    'shrink-0 rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/25',
    isActive ? 'bg-foreground text-background' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
  );

function NavLinks() {
  const user = useAuthedUser();
  return (
    <>
      <NavLink to={paths.dashboard} className={navLinkClass}>
        {messages.nav.dashboard}
      </NavLink>
      {user.role === 'platform_admin' && (
        <NavLink to={paths.adminTenants} className={navLinkClass}>
          {messages.nav.tenants}
        </NavLink>
      )}
      {user.role === 'tenant_admin' && user.tenant_id !== null && (
        <NavLink to={paths.documents} className={navLinkClass}>
          {messages.nav.documents}
        </NavLink>
      )}
      <NavLink to={paths.profile} className={navLinkClass}>
        {messages.nav.profile}
      </NavLink>
    </>
  );
}

/** Top bar of signed-in pages; the links move to a scrollable row on phones. */
export function AppHeader() {
  const user = useAuthedUser();
  const name = user.full_name || user.email;

  return (
    <header className="sticky top-0 z-30 border-b bg-background/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-8">
          <Logo />
          <nav aria-label={messages.nav.main} className="hidden items-center gap-1 md:flex">
            <NavLinks />
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <LocaleToggle />
          <ThemeToggle />
          <NavLink
            to={paths.profile}
            aria-label={messages.nav.openProfile(name)}
            className="rounded-full focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/30"
          >
            <Avatar name={user.full_name} email={user.email} />
          </NavLink>
        </div>
      </div>
      <nav
        aria-label={messages.nav.main}
        className="flex items-center gap-1 overflow-x-auto px-4 pb-2.5 [scrollbar-width:none] md:hidden"
      >
        <NavLinks />
      </nav>
    </header>
  );
}
