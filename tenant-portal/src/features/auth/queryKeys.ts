export const authKeys = {
  all: ['auth'] as const,
  me: () => [...authKeys.all, 'me'] as const,
  tenant: () => [...authKeys.all, 'tenant'] as const,
};
