import { RouterProvider } from 'react-router-dom';
import { AppErrorBoundary } from '@/components/feedback/ErrorScreens';
import { SplashScreen } from '@/components/feedback/SplashScreen';
import { AppProviders } from './providers/AppProviders';
import { router } from './router';

export function App() {
  return (
    <AppErrorBoundary>
      <AppProviders>
        {/* Shown while the first page's chunk loads. */}
        <RouterProvider router={router} fallbackElement={<SplashScreen />} />
      </AppProviders>
    </AppErrorBoundary>
  );
}
