import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError } from 'axios'
import { useAuthStore } from '@/stores/auth'
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
