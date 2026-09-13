import axios, { AxiosError } from 'axios'

/**
 * The shared HTTP client.
 *
 * `baseURL` defaults to a same-origin `/api` — nginx proxies it in production and the
 * Vite dev server proxies it in development — so the built bundle carries no API host
 * and the same image runs anywhere.
 */
const baseURL = import.meta.env.VITE_API_BASE_URL || '/api'

const client = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
  // Load-bearing for the suite. Gecko Notes sets a session cookie on the parent
  // domain; without this, axios would not send it and every request would rely on the
  // bearer token alone.
  withCredentials: true,
})

export const AUTH_TOKEN_KEY = 'gam_auth_token'

/** localStorage can throw outright in private mode, so never let it break a request. */
export function readToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY)
  } catch {
    return null
  }
}

export function writeToken(token: string): void {
  try {
    localStorage.setItem(AUTH_TOKEN_KEY, token)
  } catch {
    /* the session cookie still carries us; a lost token is not fatal */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(AUTH_TOKEN_KEY)
  } catch {
    /* nothing to clear */
  }
}

client.interceptors.request.use((config) => {
  const token = readToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

/**
 * Pull a readable sentence out of a failure.
 *
 * The backend normalises every error to `{detail: {code, message}}`, but a request can
 * also fail before it reaches the backend — a dropped connection, a proxy error — and
 * "undefined" is not something to show a user.
 */
export function apiErrorMessage(
  error: unknown,
  fallback = 'Something went wrong'
): string {
  if (axios.isAxiosError(error)) {
    const detail = (error as AxiosError<{ detail?: { message?: string } }>).response?.data
      ?.detail
    if (detail?.message) return detail.message
    if (error.code === 'ERR_NETWORK') return 'Could not reach the server'
    if (error.message) return error.message
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

/** The machine-readable code, for branching on a specific failure. */
export function apiErrorCode(error: unknown): string | null {
  if (axios.isAxiosError(error)) {
    const detail = (error as AxiosError<{ detail?: { code?: string } }>).response?.data
      ?.detail
    return detail?.code ?? null
  }
  return null
}

export default client
