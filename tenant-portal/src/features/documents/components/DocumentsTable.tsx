import { FileSpreadsheet, FileText } from 'lucide-react';
import { messages } from '@/i18n';
import { fileExtension, formatDateTime, formatFileSize } from '../format';
import type { TenantDocument } from '../types';
import { StatusBadge } from './StatusBadge';

const t = messages.documents;

const SPREADSHEET_TYPES = new Set(['XLSX', 'CSV']);

function FileIcon({ extension }: { extension: string }) {
  const Icon = SPREADSHEET_TYPES.has(extension) ? FileSpreadsheet : FileText;
  return <Icon className="size-5 shrink-0 text-muted-foreground" aria-hidden />;
}

const th = 'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground';

export function DocumentsTable({ documents }: { documents: TenantDocument[] }) {
  return (
    <div className="-mx-2 overflow-x-auto">
      <table className="w-full min-w-[20rem] text-sm">
        <caption className="sr-only">{t.listTitle}</caption>
        <thead className="border-b">
          <tr>
            <th scope="col" className={th}>
              {t.table.name}
            </th>
            <th scope="col" className={`${th} hidden sm:table-cell`}>
              {t.table.type}
            </th>
            <th scope="col" className={`${th} hidden text-right sm:table-cell`}>
              {t.table.size}
            </th>
            <th scope="col" className={`${th} hidden md:table-cell`}>
              {t.table.uploaded}
            </th>
            <th scope="col" className={th}>
              {t.table.status}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {documents.map((doc) => {
            const extension = fileExtension(doc.original_filename);
            return (
              <tr key={doc.id}>
                <td className="max-w-0 px-3 py-3 sm:max-w-xs">
                  <div className="flex items-center gap-3">
                    <FileIcon extension={extension} />
                    <div className="min-w-0">
                      <p className="truncate font-medium" title={doc.original_filename}>
                        {doc.original_filename}
                      </p>
                      {/* Small screens hide the Type and Size columns, so show them here. */}
                      <p className="text-xs text-muted-foreground sm:hidden">
                        {extension} · {formatFileSize(doc.file_size)}
                      </p>
                      {doc.ingestion_status === 'failed' && doc.error_message && (
                        <p className="mt-0.5 text-xs text-destructive">{doc.error_message}</p>
                      )}
                    </div>
                  </div>
                </td>
                <td className="hidden px-3 py-3 text-muted-foreground sm:table-cell">{extension || '—'}</td>
                <td className="hidden px-3 py-3 text-right tabular-nums text-muted-foreground sm:table-cell">
                  {formatFileSize(doc.file_size)}
                </td>
                <td className="hidden px-3 py-3 text-muted-foreground md:table-cell">
                  {formatDateTime(doc.created_at)}
                </td>
                <td className="px-3 py-3">
                  <StatusBadge status={doc.ingestion_status} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
