import { UploadCloud } from 'lucide-react';
import { useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { messages } from '@/i18n';
import { env } from '@/lib/env';
import { toApiError, userMessage } from '@/lib/errors';
import { cn } from '@/lib/utils';
import { useUploadDocument } from '../hooks/useUploadDocument';
import { ACCEPT, validateFile, type FileProblem } from '../validation';

const t = messages.documents.upload;

interface Progress {
  name: string;
  /** 0..1 */
  fraction: number;
  /** 1-based position when several files are uploaded in a row. */
  index: number;
  total: number;
}

function problemText(problem: FileProblem): string {
  switch (problem) {
    case 'unsupported':
      return t.problems.unsupported;
    case 'empty':
      return t.problems.empty;
    case 'tooLarge':
      return t.problems.tooLarge(env.maxUploadMb);
    case 'nameTooLong':
      return t.problems.nameTooLong;
  }
}

/** The service reports file problems under `file`; anything else gets generic copy. */
function uploadErrorText(error: unknown): string {
  return toApiError(error).fieldErrors.file?.[0] ?? userMessage(error);
}

export function UploadCard() {
  const upload = useUploadDocument();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const busy = progress !== null;

  /** One request per file (the service takes a single file), one after the other. */
  async function uploadFiles(files: File[]) {
    for (const [i, file] of files.entries()) {
      const problem = validateFile(file);
      if (problem) {
        toast.error(t.failed(file.name), { description: problemText(problem) });
        continue;
      }
      const base = { name: file.name, index: i + 1, total: files.length };
      setProgress({ ...base, fraction: 0 });
      try {
        await upload.mutateAsync({
          file,
          onProgress: (fraction) => setProgress({ ...base, fraction }),
        });
        toast.success(t.uploaded(file.name));
      } catch (error) {
        toast.error(t.failed(file.name), { description: uploadErrorText(error) });
      }
    }
    setProgress(null);
  }

  function handleSelect(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    // Reset so choosing the same file again still fires `change`.
    event.target.value = '';
    if (files.length > 0 && !busy) void uploadFiles(files);
  }

  function handleDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0 && !busy) void uploadFiles(files);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t.title}</CardTitle>
      </CardHeader>
      <CardContent>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          tabIndex={-1}
          aria-hidden
          className="sr-only"
          onChange={handleSelect}
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
          }}
          onDrop={handleDrop}
          className={cn(
            'flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors',
            'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring/30',
            'disabled:cursor-progress',
            dragging ? 'border-primary bg-primary/5' : 'border-input hover:border-foreground/30 hover:bg-muted/50',
          )}
        >
          <UploadCloud className="size-8 text-muted-foreground" aria-hidden />
          <span className="font-semibold">{dragging ? t.dropActive : t.drop}</span>
          <span className="text-sm text-muted-foreground">{t.hint(env.maxUploadMb)}</span>
        </button>

        {progress && (
          <div className="mt-4 space-y-2" role="status" aria-live="polite">
            <div className="flex items-center justify-between gap-4 text-sm">
              <span className="min-w-0 truncate">{t.uploading(progress.name, progress.index, progress.total)}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {Math.round(progress.fraction * 100)}%
              </span>
            </div>
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress.fraction * 100)}
              aria-label={t.uploading(progress.name, progress.index, progress.total)}
              className="h-2 overflow-hidden rounded-full bg-muted"
            >
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-200"
                style={{ width: `${Math.round(progress.fraction * 100)}%` }}
              />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
