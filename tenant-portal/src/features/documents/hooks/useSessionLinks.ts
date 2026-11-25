import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createSessionLink, deactivateSessionLink, listSessionLinks } from '../api';
import { sessionLinkKeys } from '../queryKeys';

export function useSessionLinks() {
  return useQuery({
    queryKey: sessionLinkKeys.list(),
    queryFn: listSessionLinks,
    staleTime: 30_000,
  });
}

export function useCreateSessionLink() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (expiresAt: string | null) => createSessionLink(expiresAt),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionLinkKeys.list() }),
  });
}

export function useDeactivateSessionLink() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deactivateSessionLink(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionLinkKeys.list() }),
  });
}
