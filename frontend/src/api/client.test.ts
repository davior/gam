import { AxiosError } from 'axios'
import { describe, expect, it } from 'vitest'
import { apiErrorCode, apiErrorMessage } from '@/api/client'

function axiosErrorWith(data: unknown, code?: string): AxiosError {
  const error = new AxiosError('Request failed', code)
  // Minimal shape — only what the helpers actually read.
  error.response = { data, status: 400, statusText: '', headers: {}, config: {} as never }
  return error
}

describe('apiErrorMessage', () => {
  it('prefers the backend message', () => {
    const error = axiosErrorWith({ detail: { code: 'too_large', message: 'File is too big' } })
    expect(apiErrorMessage(error)).toBe('File is too big')
  })

  it('explains an unreachable server rather than showing a library string', () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK')
    expect(apiErrorMessage(error)).toBe('Could not reach the server')
  })

  it('falls back when the response carries no envelope', () => {
    const error = axiosErrorWith({ something: 'unexpected' })
    expect(apiErrorMessage(error)).toBe('Request failed')
  })

  it('handles a plain Error', () => {
    expect(apiErrorMessage(new Error('boom'))).toBe('boom')
  })

  it('never returns undefined for a non-error', () => {
    expect(apiErrorMessage(null)).toBe('Something went wrong')
    expect(apiErrorMessage(undefined, 'custom')).toBe('custom')
  })
})

describe('apiErrorCode', () => {
  it('extracts the machine-readable code', () => {
    const error = axiosErrorWith({ detail: { code: 'unauthorized', message: 'nope' } })
    expect(apiErrorCode(error)).toBe('unauthorized')
  })

  it('is null when there is no envelope', () => {
    expect(apiErrorCode(new Error('boom'))).toBeNull()
    expect(apiErrorCode(axiosErrorWith({}))).toBeNull()
  })
})
