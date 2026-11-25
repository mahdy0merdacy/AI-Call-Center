import { Toaster as SonnerToaster } from 'sonner';
import { useTheme } from '@/app/providers/ThemeProvider';

export function Toaster() {
  const { resolvedTheme } = useTheme();
  return (
    <SonnerToaster
      theme={resolvedTheme}
      position="top-right"
      closeButton
      richColors
      toastOptions={{
        classNames: {
          toast: 'font-sans rounded-xl border shadow-card',
          title: 'font-semibold',
        },
      }}
    />
  );
}
