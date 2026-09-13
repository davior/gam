import client from '@/api/client'

/**
 * Identity comes from Gecko Notes, not from here.
 *
 * GAM has no login form, no registration and no password reset. Notes signs the token;
 * this module verifies who that makes us, and sends the browser to Notes when nobody
 * is signed in.
 */

export interface User {
  id: string
  username: string
}

interface DataResponse<T> {
  data: T
}

export const authApi = {
  /** Who the backend thinks we are. 401 means not signed in. */
  me(): Promise<User> {
    return client.get<DataResponse<User>>('/me').then((r) => r.data.data)
  },
}

/** Where the suite's sign-in page lives. Same origin in dev, Notes in production. */
export function notesBaseUrl(): string {
  return import.meta.env.VITE_NOTES_BASE_URL || 'https://notes.geckopico.com'
}

/**
 * Hand the browser to Gecko Notes to sign in, asking to be sent back here.
 *
 * The return URL is passed as a parameter rather than remembered on Notes' side so
 * this works for any app in the suite without Notes knowing about them. Notes
 * validates it against a *.geckopico.com allowlist — see GN-2 — which is what keeps
 * this from being an open redirect.
 */
export function redirectToLogin(): void {
  const target = encodeURIComponent(window.location.href)
  window.location.href = `${notesBaseUrl()}/login?redirect=${target}`
}
