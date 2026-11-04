import type { ReactNode } from 'react';
import { Logo } from '@/components/brand/Logo';
import { MobileBrandHeader, BrandPanel } from '@/components/layout/BrandPanel';
import { Stagger, StaggerItem } from '@/components/motion';
import { LocaleToggle } from '@/components/LocaleToggle';
import { ThemeToggle } from '@/components/ThemeToggle';
import { messages } from '@/i18n';
import { env } from '@/lib/env';
import { useDocumentTitle } from '@/lib/hooks/useDocumentTitle';
import { cn } from '@/lib/utils';

const footerLinks = [
  { href: env.privacyUrl, label: messages.footer.privacy },
  { href: env.termsUrl, label: messages.footer.terms },
  { href: env.supportEmail && `mailto:${env.supportEmail}`, label: messages.footer.help },
].filter((link): link is { href: string; label: string } => Boolean(link.href));

interface AuthLayoutProps {
  /** Page heading (rendered as the page's single <h1>). */
  title?: ReactNode;
  description?: ReactNode;
  /** Optional illustration/icon above the title (e.g. animated check). */
  icon?: ReactNode;
  children: ReactNode;
  /** Content under the card, e.g. "Don't have an account? Sign up". */
  footer?: ReactNode;
  /** Wider card for multi-step flows (MFA setup, backup codes). */
  wide?: boolean;
  /** Browser tab title; defaults to `title` when it is a string. */
  documentTitle?: string;
  className?: string;
}

/**
 * Split auth layout: form card on the left, animated brand panel on the
 * right. Below `lg` the panel collapses into a compact gradient header.
 */
export function AuthLayout({
  title,
  description,
  icon,
  children,
  footer,
  wide = false,
  documentTitle,
  className,
}: AuthLayoutProps) {
  useDocumentTitle(documentTitle ?? (typeof title === 'string' ? title : null));

  return (
    <div className="grid min-h-dvh bg-background lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <div className="relative flex min-w-0 flex-col">
        <MobileBrandHeader className="lg:hidden" />

        <header className="hidden items-center justify-between px-10 py-6 lg:flex">
          <Logo />
          <div className="flex items-center gap-2">
            <LocaleToggle />
            <ThemeToggle />
          </div>
        </header>

        <main
          id="main"
          tabIndex={-1}
          className="flex flex-1 items-start justify-center px-4 pb-10 pt-6 focus:outline-none sm:items-center sm:px-10 lg:pt-0"
        >
          <div className={cn('w-full', wide ? 'max-w-[560px]' : 'max-w-[440px]', className)}>
            <Stagger className="rounded-2xl border-border/70 sm:border sm:bg-card sm:p-9 sm:shadow-card">
              {(icon || title || description) && (
                <div className="mb-7 space-y-2">
                  {icon && <StaggerItem className="mb-5">{icon}</StaggerItem>}
                  {title && (
                    <StaggerItem>
                      <h1
                        id="page-title"
                        className="text-[1.65rem] font-bold leading-tight text-foreground"
                      >
                        {title}
                      </h1>
                    </StaggerItem>
                  )}
                  {description && (
                    <StaggerItem>
                      <p className="text-[15px] leading-relaxed text-muted-foreground">
                        {description}
                      </p>
                    </StaggerItem>
                  )}
                </div>
              )}
              {children}
            </Stagger>

            {footer && (
              <div className="mt-6 text-center text-sm text-muted-foreground">{footer}</div>
            )}
          </div>
        </main>

        <footer className="flex flex-wrap items-center justify-between gap-3 px-5 pb-6 text-xs text-muted-foreground sm:px-10">
          <span>
            © {new Date().getFullYear()} {env.appName}
          </span>
          {footerLinks.length > 0 && (
            <nav aria-label={messages.footer.legal} className="flex gap-4">
              {footerLinks.map((link) => (
                <a key={link.href} className="hover:text-foreground" href={link.href}>
                  {link.label}
                </a>
              ))}
            </nav>
          )}
        </footer>
      </div>

      <BrandPanel className="hidden lg:sticky lg:top-0 lg:flex lg:h-dvh" />
    </div>
  );
}
