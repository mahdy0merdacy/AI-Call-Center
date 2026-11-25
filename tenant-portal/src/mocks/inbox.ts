/**
 * Mock mail server: emails the mock backend "sends" (verification, password
 * reset) land here and are shown by <MockInbox>, so links can be clicked in
 * the demo without logging any token to the console.
 */
export interface MockEmail {
  id: string;
  to: string;
  subject: string;
  link: string;
  sentAt: number;
}

const STORAGE_KEY = 'msw:inbox:v1';
type Listener = () => void;
const listeners = new Set<Listener>();

function load(): MockEmail[] {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as MockEmail[];
  } catch {
    // Start empty.
  }
  return [];
}

let emails: MockEmail[] = load();

function save() {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(emails));
  } catch {
    // Ignore in the mock.
  }
  listeners.forEach((listener) => listener());
}

export const mockInbox = {
  send(to: string, subject: string, link: string): void {
    emails = [{ id: crypto.randomUUID(), to, subject, link, sentAt: Date.now() }, ...emails].slice(0, 20);
    save();
  },
  list(): MockEmail[] {
    return emails;
  },
  clear(): void {
    emails = [];
    save();
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};
