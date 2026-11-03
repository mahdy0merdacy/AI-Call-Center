/**
 * In-memory access-token holder. The token is never written to localStorage,
 * sessionStorage or cookies: a page reload drops it and the session is
 * restored from the HttpOnly refresh cookie instead.
 */
type Listener = () => void;

let accessToken: string | null = null;
const listeners = new Set<Listener>();

function emit() {
  listeners.forEach((listener) => listener());
}

export const tokenStore = {
  get(): string | null {
    return accessToken;
  },
  set(token: string): void {
    if (token === accessToken) return;
    accessToken = token;
    emit();
  },
  clear(): void {
    if (accessToken === null) return;
    accessToken = null;
    emit();
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};
