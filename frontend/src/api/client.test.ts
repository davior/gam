import { AxiosError } from 'axios'
import { describe, expect, it } from 'vitest'
import client, { apiErrorCode, apiErrorMessage } from '@/api/client'

function axiosErrorWith(data: unknown, code?: string): AxiosError {
  const error = new AxiosError('Request failed', code)
  // Minimal shape — only what the helpers actually read.
  error.response = { data, status: 400, statusText: '', headers: {}, config: {} as never }
  return error
}

describe('apiErrorMessage', () => {
  it('prefers the backend message', () => {
    const error = axiosErrorWith({
      detail: { code: 'too_large', message: 'File is too big' },
    })
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

describe('query parameter serialisation', () => {
  const serialize = (params: Record<string, unknown>) => {
    const serializer = client.defaults.paramsSerializer
    if (typeof serializer === 'function') return serializer(params)
    return serializer!.serialize!(params)
  }

  it('repeats the key for an array instead of bracketing it', () => {
    /**
     * The whole reason this serialiser exists. Axios' default emits `tag[]=a&tag[]=b`,
     * and FastAPI reads a repeatable Query parameter from repeated bare keys — so the
     * bracketed form is not rejected, it is *ignored*, and the listing comes back
     * unfiltered with a 200. A filter that silently matches everything is the worst
     * possible failure mode for this control, and it looks fine in a screenshot.
     */
    expect(serialize({ tag: ['interview', '2024'] })).toBe('tag=interview&tag=2024')
  })

  it('drops null and undefined rather than sending the word "undefined"', () => {
    expect(serialize({ q: 'nato', source: null, category_id: undefined })).toBe('q=nato')
  })

  it('encodes values that need it', () => {
    expect(serialize({ q: 'klaus schwab & co' })).toBe('q=klaus+schwab+%26+co')
  })

  it('keeps scalars alongside arrays', () => {
    expect(serialize({ tag: ['a'], limit: 60, min_duration: 0 })).toBe(
      'tag=a&limit=60&min_duration=0'
    )
  })
})
