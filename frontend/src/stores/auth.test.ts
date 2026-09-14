import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError } from 'axios'
import { useAuthStore } from '@/stores/auth'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'
import * as auth from '@/api/auth'
import { authApi } from '@/api/auth'

function unauthorizedError(): AxiosError {
  const error = new AxiosError('Unauthorized')
  error.response = {
    data: { detail: { code: 'unauthorized', message: 'Missing bearer token' } },
    status: 401,
    statusText: '',
    headers: {},
    config: {} as never,
  }
  return error
}

beforeEach(() => {
  useAuthStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('auth store bootstrap', () => {
  it('authenticates when the session is valid', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({ id: 'u1', username: 'davior' })

    await useAuthStore.getState().bootstrap()

    const state = useAuthStore.getState()
    expect(state.status).toBe('authenticated')
    expect(state.user?.username).toBe('davior')
    expect(state.error).toBeNull()
  })

  it('goes anonymous with no error on a 401', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(unauthorizedError())

    await useAuthStore.getState().bootstrap()

    const state = useAuthStore.getState()
    expect(state.status).toBe('anonymous')
    expect(state.error).toBeNull()
  })

  it('distinguishes an unreachable backend from a signed-out user', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue(
      new AxiosError('Network Error', 'ERR_NETWORK')
    )

    await useAuthStore.getState().bootstrap()

    const state = useAuthStore.getState()
    expect(state.status).toBe('anonymous')
    expect(state.error).toMatch(/could not reach/i)
  })

  it('does not fire a second request while one is in flight', async () => {
    // React 18 StrictMode mounts effects twice in development, so this is the normal
    // case rather than an edge one.
    const me = vi
      .spyOn(authApi, 'me')
      .mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(() => resolve({ id: 'u1', username: 'a' }), 10)
          )
      )

    await Promise.all([
      useAuthStore.getState().bootstrap(),
      useAuthStore.getState().bootstrap(),
    ])

    expect(me).toHaveBeenCalledTimes(1)
  })
})

describe('signing out', () => {
  /**
   * Everything holding one person's data has to be emptied here, not just the obvious
   * one. GAM runs on a shared parent domain and signing out is the only boundary
   * between two people on the same browser — a store left populated is the previous
   * user's data sitting in front of the next one.
   */
  it('empties every store that holds user data', () => {
    vi.spyOn(auth, 'redirectToLogin').mockResolvedValue(undefined)

    useLibraryStore.setState({ total: 7, query: 'nato' })
    useTagStore.setState({
      tags: [{ id: 't1', name: 'Klaus Schwab', category_id: null }],
      categories: [{ id: 'c1', name: 'People', parent_category_id: null }],
      loaded: true,
    })

    useAuthStore.getState().signOut()

    expect(useLibraryStore.getState().total).toBe(0)
    expect(useLibraryStore.getState().query).toBe('')
    expect(useTagStore.getState().tags).toEqual([])
    expect(useTagStore.getState().categories).toEqual([])
    // `loaded` too, or the next person's catalogue is never fetched and their tag box
    // silently offers nothing.
    expect(useTagStore.getState().loaded).toBe(false)
  })
})
