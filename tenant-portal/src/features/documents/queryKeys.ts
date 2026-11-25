export const sessionLinkKeys = {
  all: ['session-links'] as const,
  list: () => [...sessionLinkKeys.all, 'list'] as const,
};

export const documentKeys = {
  all: ['documents'] as const,
  list: () => [...documentKeys.all, 'list'] as const,
};
