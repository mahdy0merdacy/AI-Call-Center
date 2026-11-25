/**
 * App-wide auth signals raised outside React (axios interceptors, timers) and
 * consumed by the router layer, which decides where to navigate.
 */
export type AuthEvent = 'session-expired' | 'tenant-suspended' | 'signed-out';

type Handler = () => void;

const handlers = new Map<AuthEvent, Set<Handler>>();

export const authEvents = {
  emit(event: AuthEvent): void {
    handlers.get(event)?.forEach((handler) => handler());
  },
  on(event: AuthEvent, handler: Handler): () => void {
    const set = handlers.get(event) ?? new Set<Handler>();
    set.add(handler);
    handlers.set(event, set);
    return () => {
      set.delete(handler);
    };
  },
};

/** Cross-tab broadcast so signing out in one tab signs out the others. */
const CHANNEL_NAME = 'tp-auth';
const channel: BroadcastChannel | null =
  typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(CHANNEL_NAME);

export function broadcastSignOut(): void {
  channel?.postMessage('signed-out');
}

export function onRemoteSignOut(handler: Handler): () => void {
  if (!channel) return () => undefined;
  const listener = (event: MessageEvent<unknown>) => {
    if (event.data === 'signed-out') handler();
  };
  channel.addEventListener('message', listener);
  return () => channel.removeEventListener('message', listener);
}
