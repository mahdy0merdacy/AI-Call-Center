/**
 * Sends a company admin to the media edge's browser test page for their own
 * agent (`/w/<slug>`), so a new client can call it right after signing up.
 */
import { ExternalLink, PhoneCall } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { messages } from '@/i18n';
import { env } from '@/lib/env';

const t = messages.dashboard.testAgent;
const HINT_ID = 'test-agent-hint';

interface TestAgentCardProps {
  /** The tenant's slug, null when it couldn't be loaded. */
  slug: string | null;
  loading: boolean;
}

export function TestAgentCard({ slug, loading }: TestAgentCardProps) {
  const url = env.voiceTestUrl && slug ? `${env.voiceTestUrl}/w/${encodeURIComponent(slug)}` : null;
  const hint = !env.voiceTestUrl ? t.notConfigured : !slug && !loading ? t.noSlug : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t.title}</CardTitle>
        <CardDescription>{t.description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <dl className="min-w-0 space-y-1">
            <dt className="text-sm text-muted-foreground">{t.slugLabel}</dt>
            <dd>
              {loading ? (
                <Skeleton className="h-6 w-32" />
              ) : (
                <code className="rounded bg-muted px-2 py-1 text-xs">{slug ?? '—'}</code>
              )}
            </dd>
          </dl>
          {url ? (
            <Button asChild>
              <a href={url} target="_blank" rel="noopener noreferrer">
                <PhoneCall className="size-4" aria-hidden />
                {t.open}
                <ExternalLink className="size-4" aria-hidden />
                <span className="sr-only">{t.newTab}</span>
              </a>
            </Button>
          ) : (
            <Button disabled aria-describedby={hint ? HINT_ID : undefined}>
              <PhoneCall className="size-4" aria-hidden />
              {t.open}
            </Button>
          )}
        </div>
        {hint && (
          <p id={HINT_ID} className="text-sm text-muted-foreground">
            {hint}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
