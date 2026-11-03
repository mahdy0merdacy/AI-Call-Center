/**
 * Connectivity as the app sees it: the browser's online flag, plus whether
 * the API answered the last request (a server can be down while we're online).
 * Updated by the axios interceptors and the window online/offline events.
 */
export interface NetworkState {
  online: boolean;
  serverReachable: boolean;
}

type Listener = () => void;

let state: NetworkState = {
  online: typeof navigator === 'undefined' ? true : navigator.onLine,
  serverReachable: true,
};
const listeners = new Set<Listener>();

function update(next: Partial<NetworkState>) {
  const merged = { ...state, ...next };
  if (merged.online === state.online && merged.serverReachable === state.serverReachable) return;
  state = merged;
  listeners.forEach((listener) => listener());
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => update({ online: true }));
  window.addEventListener('offline', () => update({ online: false }));
}

export const networkStatus = {
  get: (): NetworkState => state,
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  markReachable: () => update({ serverReachable: true }),
  markUnreachable: () => update({ serverReachable: false }),
};
