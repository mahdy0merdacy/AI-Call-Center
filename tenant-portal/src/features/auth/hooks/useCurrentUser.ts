import { useQuery } from '@tanstack/react-query';
import { fetchCurrentTenant, fetchCurrentUser } from '../api/session';
import { authKeys } from '../queryKeys';
import { useSession } from '../session/SessionProvider';

export function useCurrentUser() {
  const { status } = useSession();
  return useQuery({
    queryKey: authKeys.me(),
    queryFn: fetchCurrentUser,
    enabled: status === 'authenticated',
    staleTime: 60_000,
  });
}

export function useCurrentTenant(enabled = true) {
  const { status } = useSession();
  return useQuery({
    queryKey: authKeys.tenant(),
    queryFn: fetchCurrentTenant,
    enabled: enabled && status === 'authenticated',
    staleTime: 60_000,
  });
}
