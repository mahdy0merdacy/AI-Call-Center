import { Bug, Home, RotateCw } from 'lucide-react';
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { isRouteErrorResponse, useRouteError } from 'react-router-dom';
import { paths } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem } from '@/components/motion';
import { Button } from '@/components/ui/button';
import { NotFoundPage } from '@/features/system/pages/NotFoundPage';
import { messages } from '@/i18n';
import { env } from '@/lib/env';

const t = messages.crash;

function describe(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  return String(error);
}

/** Router `errorElement`: a render error in any page lands here (inside the providers). */
export function RouteErrorPage() {
  const error = useRouteError();
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFoundPage />;

  return (
    <AuthLayout
      icon={
        <span className="flex size-12 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
          <Bug className="size-6" aria-hidden />
        </span>
      }
      title={t.title}
      description={t.body}
    >
      <StaggerItem className="space-y-3">
        <Button block size="lg" onClick={() => window.location.reload()}>
          <RotateCw className="size-4" aria-hidden />
          {t.reload}
        </Button>
        <Button asChild block size="lg" variant="outline">
          {/* A full load, so the broken render tree is discarded. */}
          <a href={paths.dashboard}>
            <Home className="size-4" aria-hidden />
            {t.home}
          </a>
        </Button>
        {/* Only in development: production users never see stack details. */}
        {env.isDev && (
          <details className="rounded-lg border bg-muted/40 p-3 text-xs">
            <summary className="cursor-pointer font-medium">{t.details}</summary>
            <pre className="mt-2 whitespace-pre-wrap break-words font-mono">{describe(error)}</pre>
          </details>
        )}
      </StaggerItem>
    </AuthLayout>
  );
}

interface AppErrorBoundaryState {
  error: unknown;
}

/**
 * Last line of defence around the providers themselves. Its fallback uses no
 * context (theme, router, query) because any of them may be what crashed.
 */
export class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  override state: AppErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): AppErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(_error: unknown, _info: ErrorInfo): void {
    // Hook for an error-reporting service. Never log tokens or form values here.
  }

  override render() {
    if (this.state.error === null) return this.props.children;
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background p-6 text-foreground">
        <div role="alert" className="max-w-md space-y-4 text-center">
          <h1 className="text-2xl font-bold">{t.title}</h1>
          <p className="text-muted-foreground">{t.body}</p>
          <div className="flex justify-center gap-3">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground"
            >
              {t.reload}
            </button>
            {/* Plain link: this boundary sits outside the router. */}
            <a href={paths.dashboard} className="rounded-lg border px-4 py-2 text-sm font-semibold">
              {t.home}
            </a>
          </div>
        </div>
      </div>
    );
  }
}
