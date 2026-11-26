/**
 * Sends a company admin to the media edge's browser test page for their own
 * agent (`/w/<slug>`), so a new client can call it right after signing up.
 */
import { ExternalLink, PhoneCall } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { messages } from '@/i18n';
import { env } from '@/lib/env';
import { cn } from '@/lib/utils';

const t = messages.dashboard.testAgent;
const HINT_ID = 'test-agent-hint';

interface TestAgentCardProps {
  /** The tenant's slug, null when it couldn't be loaded. */
  slug: string | null;
  loading: boolean;
}

/** A handset icon with rings pulsing out of it while the call is available. */
function RingingIcon({ ringing }: { ringing: boolean }) {
  return (
    <span className="relative flex size-9 items-center justify-center rounded-full bg-primary-foreground/15">
      {ringing && (
        <>
          <span className="absolute inset-0 animate-ring rounded-full border border-primary-foreground/60" />
          <span className="absolute inset-0 animate-ring rounded-full border border-primary-foreground/40 [animation-delay:1.1s]" />
        </>
      )}
      <PhoneCall className="size-4" aria-hidden />
    </span>
  );
}

export function TestAgentCard({ slug, loading }: TestAgentCardProps) {
  const url = env.voiceTestUrl && slug ? `${env.voiceTestUrl}/w/${encodeURIComponent(slug)}` : null;
  const hint = !env.voiceTestUrl ? t.notConfigured : !slug && !loading ? t.noSlug : null;
  const buttonClass = 'h-14 rounded-full pl-2.5 pr-6 text-[15px]';

  return (
    <div className="space-y-4">
      <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
        {t.title}
      </h2>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
        {url ? (
          <Button asChild size="lg" className={buttonClass}>
            <a href={url} target="_blank" rel="noopener noreferrer">
              <RingingIcon ringing />
              {t.open}
              <ExternalLink className="size-4 opacity-70" aria-hidden />
              <span className="sr-only">{t.newTab}</span>
            </a>
          </Button>
        ) : (
          <Button disabled size="lg" className={buttonClass} aria-describedby={hint ? HINT_ID : undefined}>
            <RingingIcon ringing={false} />
            {t.open}
          </Button>
        )}
        <dl className="min-w-0 space-y-1">
          <dt className="text-xs text-muted-foreground">{t.slugLabel}</dt>
          <dd>
            {loading ? (
              <Skeleton className="h-5 w-28" />
            ) : (
              <code className={cn('font-mono text-[13px]', !slug && 'text-muted-foreground')}>
                {slug ? `/w/${slug}` : '—'}
              </code>
            )}
          </dd>
        </dl>
      </div>
      <p className="max-w-md text-sm text-muted-foreground">{t.description}</p>
      {hint && (
        <p id={HINT_ID} className="max-w-md text-sm text-warning">
          {hint}
        </p>
      )}
    </div>
  );
}
