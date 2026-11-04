import type { FieldValues, Path, UseFormSetError } from 'react-hook-form';
import type { ApiRequestError } from '@/lib/errors';

interface ApplyOptions<T extends FieldValues> {
  /** Form fields that may receive server errors. */
  fields: readonly Path<T>[];
  /** Backend field name -> form field name, when they differ (e.g. new_password -> password). */
  aliases?: Partial<Record<string, Path<T>>>;
}

/**
 * Maps a DRF validation error onto react-hook-form fields. Returns the
 * message that could not be attached to a field (non_field_errors, detail,
 * or errors for unknown fields) so the caller can show it at form level.
 */
export function applyServerErrors<T extends FieldValues>(
  error: ApiRequestError,
  setError: UseFormSetError<T>,
  { fields, aliases = {} }: ApplyOptions<T>,
): string | null {
  const leftovers: string[] = [...error.formErrors];
  let focused = false;

  for (const [serverField, list] of Object.entries(error.fieldErrors)) {
    const target = aliases[serverField] ?? (serverField as Path<T>);
    if (fields.includes(target)) {
      setError(target, { type: 'server', message: list.join(' ') }, { shouldFocus: !focused });
      focused = true;
    } else {
      leftovers.push(...list);
    }
  }

  if (leftovers.length > 0) return leftovers.join(' ');
  if (!focused && error.kind !== 'validation') return error.message;
  return null;
}
