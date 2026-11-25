/**
 * Dev-only floating panel (mock mode). Two tabs:
 *  - Scenarios: trigger session/network edge cases on demand.
 *  - Inbox: emails the mock backend "sent" (verification, reset links).
 * Lazy-loaded by RootLayout only when VITE_ENABLE_MSW=true.
 */
import { useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { FlaskConical, Inbox, Trash2, X } from 'lucide-react';
import { useState, useSyncExternalStore, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { paths } from '@/app/paths';
import { useCurrentUser } from '@/features/auth/hooks/useCurrentUser';
import { authKeys } from '@/features/auth/queryKeys';
import { useSession } from '@/features/auth/session/SessionProvider';
import { tokenStore } from '@/lib/auth/tokenStore';
import { cn } from '@/lib/utils';
import { db, endSession, findTenant, findUserById, persist, resetMockDb } from './db';
import { mockInbox } from './inbox';
import { mintAccessToken } from './jwt';
import { mockNetwork } from './network';

function timeAgo(timestamp: number): string {
  const seconds = Math.round((Date.now() - timestamp) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.round(seconds / 60)}m ago`;
}

function Action({
  title,
  detail,
  onClick,
  disabled,
  active,
}: {
  title: string;
  detail: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-pressed={active}
        className={cn(
          'w-full px-4 py-2.5 text-left transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-45',
          active && 'bg-warning/10',
        )}
      >
        <span className="block text-sm font-medium">{title}</span>
        <span className="block text-xs text-muted-foreground">{detail}</span>
      </button>
    </li>
  );
}

function Scenarios({ onDone }: { onDone: () => void }) {
  const { status } = useSession();
  const me = useCurrentUser();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const networkDown = useSyncExternalStore(mockNetwork.subscribe, mockNetwork.isDown);
  const user = me.data ? findUserById(me.data.id) : undefined;
  const signedIn = status === 'authenticated' && user !== undefined;

  const refetchSession = () => void queryClient.invalidateQueries({ queryKey: authKeys.all });

  const run = (fn: () => void, message?: string) => {
    fn();
    if (message) toast.info(message);
    onDone();
  };

  return (
    <ul className="divide-y">
      <Action
        title="Expire the access token"
        detail="Next requests get 401 → one shared refresh → requests replayed"
        disabled={!signedIn}
        onClick={() =>
          run(() => {
            if (!user) return;
            tokenStore.set(mintAccessToken(user, -60));
            refetchSession(); // /auth/me and /tenant/me fire in parallel
          }, 'Access token expired: check the Network tab for a single /refresh/.')
        }
      />
      <Action
        title="Revoke the refresh session"
        detail="Refresh fails → forced sign-out with “session expired”"
        disabled={!signedIn}
        onClick={() =>
          run(() => {
            if (!user) return;
            endSession();
            tokenStore.set(mintAccessToken(user, -60));
            refetchSession();
          })
        }
      />
      <Action
        title="Suspend my tenant"
        detail="Backend blocks the next request (403 tenant_suspended)"
        disabled={!signedIn || !user?.tenant_id}
        onClick={() =>
          run(() => {
            const tenant = findTenant(user?.tenant_id ?? null);
            if (!tenant) return;
            tenant.status = 'suspended';
            persist();
            refetchSession();
          })
        }
      />
      <Action
        title={networkDown ? 'Restore the network' : 'Simulate network failure'}
        detail="Every API call fails like a dropped connection"
        active={networkDown}
        onClick={() =>
          run(() => {
            mockNetwork.set(!networkDown);
            if (networkDown) void queryClient.refetchQueries({ type: 'active' });
            else refetchSession();
          })
        }
      />
      <Action
        title="Throw a render error"
        detail="Shows the error boundary screen"
        onClick={() => run(() => navigate('/__mock/crash'))}
      />
      <Action
        title="Reset mock data"
        detail="Seed users and tenants, empty inbox, signed out"
        onClick={() =>
          run(() => {
            resetMockDb();
            mockInbox.clear();
            mockNetwork.set(false);
            window.location.assign(paths.login);
          })
        }
      />
      <li className="px-4 py-2 text-[11px] text-muted-foreground">
        Signed in as: {user?.email ?? 'nobody'} · sessions on server: {Object.keys(db.sessions).length}
      </li>
    </ul>
  );
}

function InboxList({ onOpen }: { onOpen: () => void }) {
  const emails = useSyncExternalStore(mockInbox.subscribe, mockInbox.list, mockInbox.list);
  const navigate = useNavigate();
  return (
    <ul className="divide-y">
      {emails.length === 0 && <li className="px-4 py-6 text-center text-sm text-muted-foreground">No emails yet.</li>}
      {emails.map((email) => (
        <li key={email.id} className="space-y-1 px-4 py-3">
          <div className="flex items-baseline justify-between gap-2">
            <p className="truncate text-sm font-medium">{email.subject}</p>
            <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(email.sentAt)}</span>
          </div>
          <p className="truncate text-xs text-muted-foreground">To: {email.to}</p>
          <button
            type="button"
            onClick={() => {
              onOpen();
              navigate(email.link);
            }}
            className="text-xs font-semibold text-primary hover:underline"
          >
            Open link →
          </button>
        </li>
      ))}
    </ul>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        'rounded-md px-2.5 py-1 text-xs font-semibold',
        active ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </button>
  );
}

export default function MockDevtools() {
  const emails = useSyncExternalStore(mockInbox.subscribe, mockInbox.list, mockInbox.list);
  const networkDown = useSyncExternalStore(mockNetwork.subscribe, mockNetwork.isDown);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'scenarios' | 'inbox'>('scenarios');

  return (
    <div className="fixed bottom-4 right-4 z-40 flex flex-col items-end font-sans print:hidden">
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            className="mb-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border bg-card shadow-card"
            role="dialog"
            aria-label="Mock devtools"
          >
            <div className="flex items-center justify-between border-b px-3 py-2">
              <div role="tablist" className="flex gap-1">
                <TabButton active={tab === 'scenarios'} onClick={() => setTab('scenarios')}>
                  Scenarios
                </TabButton>
                <TabButton active={tab === 'inbox'} onClick={() => setTab('inbox')}>
                  Inbox{emails.length > 0 ? ` (${emails.length})` : ''}
                </TabButton>
              </div>
              <div className="flex gap-1">
                {tab === 'inbox' && (
                  <button
                    type="button"
                    onClick={() => mockInbox.clear()}
                    className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label="Clear inbox"
                  >
                    <Trash2 className="size-4" aria-hidden />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label="Close mock devtools"
                >
                  <X className="size-4" aria-hidden />
                </button>
              </div>
            </div>
            <div className="max-h-[22rem] overflow-y-auto">
              {tab === 'scenarios' ? (
                <Scenarios onDone={() => setOpen(false)} />
              ) : (
                <InboxList onOpen={() => setOpen(false)} />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-2 rounded-full border border-dashed border-warning/60 bg-card px-3.5 py-2 text-xs font-semibold text-foreground shadow-card hover:bg-muted"
        aria-expanded={open}
      >
        <FlaskConical className="size-4 text-warning" aria-hidden />
        Mock devtools
        {emails.length > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-warning px-1.5 text-[11px] text-warning-foreground">
            <Inbox className="size-3" aria-hidden />
            {emails.length}
          </span>
        )}
        {networkDown && <span className="rounded-full bg-destructive px-1.5 text-[11px] text-destructive-foreground">offline</span>}
      </button>
    </div>
  );
}
