import { create } from 'zustand'
import { authApi, redirectToLogin, type User } from '@/api/auth'
import { apiErrorCode, clearToken } from '@/api/client'

/**
 * Who is signed in.
 *
 * There is no `login()` here on purpose — GAM cannot sign anybody in. The only two
 * outcomes of `bootstrap()` are "the session Notes gave us is valid" or "send them to
 * Notes".
 */

type Status = 'idle' | 'loading' | 'authenticated' | 'anonymous'

interface AuthState {
  user: User | null
  status: Status
  error: string | null
  bootstrap: () => Promise<void>
  signOut: () => void
  reset: () => void
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  status: 'idle',
  error: null,

  async bootstrap() {
    // Guard against React 18 StrictMode mounting effects twice, and against a second
    // caller arriving while the first request is still out.
    if (get().status === 'loading') return
    set({ status: 'loading', error: null })

    try {
      const user = await authApi.me()
      set({ user, status: 'authenticated', error: null })
    } catch (error) {
      if (apiErrorCode(error) === 'unauthorized') {
        set({ user: null, status: 'anonymous', error: null })
        return
      }
      // A backend that is down is not the same as a user who is signed out, and
      // bouncing them to a login page would hide the real problem.
      set({
        user: null,
        status: 'anonymous',
        error: 'Could not reach the asset library. Is the API running?',
      })
    }
  },

  signOut() {
    clearToken()
    get().reset()
    redirectToLogin()
  },

  reset() {
    set({ user: null, status: 'idle', error: null })
  },
}))
