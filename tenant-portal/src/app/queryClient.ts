import { QueryClient } from '@tanstack/react-query';
import { toApiError } from '@/lib/errors';

const MAX_RETRIES = 2;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Only retry transient failures; 4xx responses are final.
      retry: (failureCount, error) => {
        const { kind } = toApiError(error);
        const transient = kind === 'network' || kind === 'timeout' || kind === 'server';
        return transient && failureCount < MAX_RETRIES;
      },
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});
