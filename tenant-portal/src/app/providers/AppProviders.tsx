import { QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'framer-motion';
import type { ReactNode } from 'react';
import { Toaster } from '@/components/ui/sonner';
import { SessionProvider } from '@/features/auth/session/SessionProvider';
import { queryClient } from '../queryClient';
import { ThemeProvider } from './ThemeProvider';

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        {/* "user" = honour prefers-reduced-motion for every framer animation. */}
        <MotionConfig reducedMotion="user">
          <SessionProvider>{children}</SessionProvider>
          <Toaster />
        </MotionConfig>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
