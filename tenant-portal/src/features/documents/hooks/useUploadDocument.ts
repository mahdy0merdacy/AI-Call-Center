import { useMutation, useQueryClient } from '@tanstack/react-query';
import { uploadDocument } from '../api';
import { documentKeys } from '../queryKeys';

interface UploadVariables {
  file: File;
  onProgress?: (fraction: number) => void;
}

export function useUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, onProgress }: UploadVariables) => uploadDocument(file, onProgress),
    // The new document shows up in the list without a manual refresh.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: documentKeys.list() }),
  });
}
