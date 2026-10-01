const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';
export const ACCESS_TOKEN_KEY = 'evfleet-token';
const SESSION_EXPIRED_EVENT = 'evfleet:session-expired';

export function storeAccessToken(token: string): void {
  window.localStorage.setItem(ACCESS_TOKEN_KEY, token);
}

export function clearAccessToken(): void {
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
}

export async function apiRequest(
  path: string,
  init: RequestInit = {},
  publicRequest = false,
): Promise<Response> {
  const headers = new Headers(init.headers);
  let tokenUsed: string | null = null;
  if (publicRequest) {
    headers.delete('Authorization');
  } else {
    const token = window.localStorage.getItem(ACCESS_TOKEN_KEY);
    if (!token) {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
      throw new Error('An authenticated session is required.');
    }
    tokenUsed = token;
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  // Several old-token requests may fail concurrently. A late 401 from one of
  // them must not clear a newer token issued after the user signed in again.
  if (!publicRequest && response.status === 401 && tokenUsed
      && window.localStorage.getItem(ACCESS_TOKEN_KEY) === tokenUsed) {
    clearAccessToken();
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  return response;
}

export function onSessionExpired(handler: () => void): () => void {
  window.addEventListener(SESSION_EXPIRED_EVENT, handler);
  return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handler);
}
