import { useQuery } from '@tanstack/react-query';
import { listDocuments } from '../api';
import { documentKeys } from '../queryKeys';

export function useDocuments() {
  return useQuery({
    queryKey: documentKeys.list(),
    queryFn: listDocuments,
    staleTime: 30_000,
  });
}
