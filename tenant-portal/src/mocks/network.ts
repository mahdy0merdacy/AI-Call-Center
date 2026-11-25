import { HttpResponse, http } from 'msw';
import { apiUrl } from './utils';

/** Devtools switch: when on, every API call fails like a dropped connection. */
let networkDown = false;
type Listener = () => void;
const listeners = new Set<Listener>();

export const mockNetwork = {
  isDown: () => networkDown,
  set(down: boolean) {
    networkDown = down;
    listeners.forEach((listener) => listener());
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

/** Must be the first handler: returns a network error, or falls through (undefined). */
export const networkFailureHandler = http.all(apiUrl('/*'), () => (networkDown ? HttpResponse.error() : undefined));
