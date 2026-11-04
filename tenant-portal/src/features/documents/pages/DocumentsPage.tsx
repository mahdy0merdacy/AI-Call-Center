/**
 * Knowledge documents a tenant uploads for its voice agent, plus the session
 * links it shares with customers. Built step by step; this step wires the page
 * to the documents service and shows loading / empty / error states.
 */
import { AppHeader } from '@/components/layout/AppHeader';
import { Stagger, StaggerItem } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { messages } from '@/i18n';
import { userMessage } from '@/lib/errors';
import { useDocumentTitle } from '@/lib/hooks/useDocumentTitle';
import { UploadCard } from '../components/UploadCard';
import { useDocuments } from '../hooks/useDocuments';

const t = messages.documents;

export function DocumentsPage() {
  const documents = useDocuments();
  useDocumentTitle(t.title);

  return (
    <div className="min-h-dvh bg-background">
      <AppHeader />
      <main id="main" tabIndex={-1} className="mx-auto max-w-5xl px-4 py-10 focus:outline-none sm:px-6">
        <Stagger className="space-y-6">
          <StaggerItem>
            <h1 className="text-3xl font-bold">{t.title}</h1>
            <p className="mt-1 text-muted-foreground">{t.description}</p>
          </StaggerItem>

          <StaggerItem>
            <UploadCard />
          </StaggerItem>

          <StaggerItem>
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle>{t.listTitle}</CardTitle>
                {documents.data && (
                  <span className="text-sm text-muted-foreground">{t.count(documents.data.count)}</span>
                )}
              </CardHeader>
              <CardContent>
                {documents.isPending ? (
                  <div className="space-y-3" aria-busy="true" aria-label={messages.common.loading}>
                    <Skeleton className="h-4 w-48" />
                    <Skeleton className="h-4 w-72" />
                  </div>
                ) : documents.isError ? (
                  <Alert
                    variant="destructive"
                    title={t.loadFailed}
                    aside={
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void documents.refetch()}
                        loading={documents.isFetching}
                      >
                        {t.retry}
                      </Button>
                    }
                  >
                    {userMessage(documents.error)}
                  </Alert>
                ) : documents.data.count === 0 ? (
                  <p className="text-sm text-muted-foreground">{t.empty}</p>
                ) : (
                  // Placeholder until the table lands in the next step.
                  <ul className="divide-y text-sm">
                    {documents.data.results.map((doc) => (
                      <li key={doc.id} className="py-2">
                        {doc.original_filename}
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </StaggerItem>
        </Stagger>
      </main>
    </div>
  );
}
