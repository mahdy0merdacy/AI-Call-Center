/**
 * Quick checks before sending a file, so obvious mistakes fail instantly. The
 * documents service repeats them (and also sniffs the file's content), so it
 * stays the source of truth; keep this list in sync with its ALLOWED_TYPES.
 */
import { env } from '@/lib/env';

export const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.xlsx', '.csv', '.txt'] as const;

/** Value for the file input's `accept` attribute. */
export const ACCEPT = ALLOWED_EXTENSIONS.join(',');

/** Matches `Document.original_filename` (max_length 255). */
const MAX_FILENAME_LENGTH = 255;

export type FileProblem = 'unsupported' | 'empty' | 'tooLarge' | 'nameTooLong';

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

export function validateFile(file: File): FileProblem | null {
  if (file.name.length > MAX_FILENAME_LENGTH) return 'nameTooLong';
  if (!(ALLOWED_EXTENSIONS as readonly string[]).includes(extensionOf(file.name))) return 'unsupported';
  if (file.size === 0) return 'empty';
  if (file.size > env.maxUploadMb * 1024 * 1024) return 'tooLarge';
  return null;
}
