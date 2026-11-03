import { useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { CloudOff, RefreshCw, WifiOff } from 'lucide-react';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { toast } from 'sonner';
import { messages } from '@/i18n';
import { networkStatus } from '@/lib/networkStatus';

const t = messages.network;

/** Slides in at the top when the browser is offline or the API stops answering. */
export function NetworkBanner() {
  const { online, serverReachable } = useSyncExternalStore(networkStatus.subscribe, networkStatus.get);
  const queryClient = useQueryClient();
  const [retrying, setRetrying] = useState(false);
  const wasOffline = useRef(!online);

  useEffect(() => {
    if (!online) {
      wasOffline.current = true;
    } else if (wasOffline.current) {
      wasOffline.current = false;
      toast.success(t.backOnline);
    }
  }, [online]);

  const problem = !online ? 'offline' : !serverReachable ? 'unreachable' : null;

  const retry = async () => {
    setRetrying(true);
    try {
      await queryClient.refetchQueries({ type: 'active' });
    } finally {
      setRetrying(false);
    }
  };

  return (
    <AnimatePresence>
      {problem && (
        <motion.div
          role="status"
          aria-live="polite"
          initial={{ y: -48, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: -48, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 380, damping: 32 }}
          className="fixed inset-x-0 top-0 z-[60] flex justify-center px-3 pt-3"
        >
          <div className="flex max-w-xl items-center gap-3 rounded-xl border border-warning/40 bg-card px-4 py-2.5 text-sm shadow-card">
            {problem === 'offline' ? (
              <WifiOff className="size-4 shrink-0 text-warning" aria-hidden />
            ) : (
              <CloudOff className="size-4 shrink-0 text-warning" aria-hidden />
            )}
            <span className="font-medium">{problem === 'offline' ? t.offline : t.unreachable}</span>
            {problem === 'unreachable' && (
              <button
                type="button"
                onClick={() => void retry()}
                disabled={retrying}
                className="ml-1 inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 font-semibold text-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-60"
              >
                <RefreshCw className={retrying ? 'size-3.5 animate-spin' : 'size-3.5'} aria-hidden />
                {t.retry}
              </button>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
