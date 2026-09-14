import { create } from 'zustand'
import { authApi, redirectToLogin, type User } from '@/api/auth'
import { apiErrorCode, clearToken } from '@/api/client'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'

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
    // Fan out to every store holding user data, so signing out cannot leave one
    // person's library on screen for the next. The tag store counts: a vocabulary of
    // names someone chose is as personal as the assets they filed under them, and it
    // would otherwise sit in the next person's autocomplete.
    useLibraryStore.getState().reset()
    useTagStore.getState().reset()
    void redirectToLogin()
  },

  reset() {
    set({ user: null, status: 'idle', error: null })
  },
}))
