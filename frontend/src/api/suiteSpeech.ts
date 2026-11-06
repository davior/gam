import axios from 'axios'
import { readToken } from '@/api/client'
import { currentConfig, loadConfig } from '@/api/config'

/**
 * Live dictation from Gecko Suite: the microphone streamed to the suite, which relays it to
 * Deepgram with its own key and sends the words back as they are heard.
 *
 * Ported from Gecko Notes' `api/deepgramStream.ts`; the protocol is the same, only the
 * address differs (the suite's stream, not Notes' own). The browser talks to the suite
 * directly, as GAM's person: a websocket cannot set headers, so a token goes in the query
 * when there is one (the shared cookie otherwise), and the suite checks it and this page's
 * origin.
 */

export type LiveDictationEvent =
  | { type: 'interim'; text: string }
  | { type: 'final'; text: string }
  | { type: 'error'; message: string }

export interface LiveDictationHandle {
  /** One MediaRecorder chunk, as a binary frame. */
  sendAudioChunk: (chunk: Blob) => void
  /** Stop speaking: the suite flushes the last words, then closes. */
  stop: () => void
  /** Close at once, nothing flushed (unmount). */
  close: () => void
}

/** The suite's close codes, as sentences. */
export const CLOSE_MESSAGES: Record<number, string> = {
  4400: 'No Deepgram key is set up in Gecko Suite, so dictation is off.',
  4401: 'Your session has ended. Sign in again to dictate.',
  4403: 'Gecko Suite refused this page. Its CORS_ORIGIN must list Asset Manager.',
  4409: 'You are already dictating somewhere else. Stop there first.',
}

function suiteBase(): string {
  return (currentConfig()?.suite_base_url ?? '').replace(/\/+$/, '')
}

export function streamUrl(base: string, token: string | null): string {
  const ws = base.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:')
  // The browser sends the shared cookie with the handshake, which the suite accepts; a
  // token goes in the address only when there is one.
  const params = new URLSearchParams({ app: 'gam' })
  if (token) params.set('token', token)
  return `${ws}/api/v1/ai/speech/stt/stream?${params.toString()}`
}

// With credentials, so the shared sign-in cookie reaches the suite whether or not this app
// holds a token: in GVC, which keeps none, leaving it out made every check a 401 and
// dictation said the session was refused.
const http = axios.create({ withCredentials: true })

/**
 * Whether dictation can be offered, and if not, why. `local` means GAM's AI does not run
 * through the suite at all, so there is nothing to offer and nothing to explain; any other
 * "no" carries a sentence, because a microphone that silently is not there gives the person
 * nothing to go on (that cost a round trip once).
 */
export type DictationReadiness =
  { state: 'ready' } | { state: 'local' } | { state: 'unavailable'; reason: string }

export const suiteSpeechApi = {
  async ready(): Promise<DictationReadiness> {
    let config
    try {
      config = await loadConfig()
    } catch {
      return { state: 'unavailable', reason: 'Could not read this app’s settings.' }
    }
    if (config.ai_backend !== 'suite' || !config.suite_base_url) return { state: 'local' }
    const token = readToken()
    try {
      const res = await http.get<{ data: { ready: boolean } }>(
        `${suiteBase()}/api/v1/ai/speech/stt/ready`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} }
      )
      return res.data.data.ready === true
        ? { state: 'ready' }
        : {
            state: 'unavailable',
            reason:
              'No Deepgram key is set up in Gecko Suite. Add one under Providers there.',
          }
    } catch (error) {
      const status = axios.isAxiosError(error) ? error.response?.status : undefined
      if (status === 401 || status === 403) {
        return {
          state: 'unavailable',
          reason: 'Gecko Suite did not accept this session. Sign out and in again.',
        }
      }
      if (status) {
        return { state: 'unavailable', reason: `Gecko Suite answered HTTP ${status}.` }
      }
      // No status at all: the browser blocked or could not make the request. Almost always
      // the suite's CORS_ORIGIN not listing this site, which is what the sentence names.
      return {
        state: 'unavailable',
        reason: `Could not reach Gecko Suite from this page. Its CORS_ORIGIN must list ${window.location.origin}.`,
      }
    }
  },
}

export function connectLiveDictation(
  onEvent: (event: LiveDictationEvent) => void,
  onClose: (code: number, reason: string) => void
): LiveDictationHandle {
  const ws = new WebSocket(streamUrl(suiteBase(), readToken()))
  // The recorder starts at once, but the handshake takes a moment. The first chunk carries
  // the audio container's header; dropped, Deepgram cannot decode the rest (Notes learnt
  // this in production), so chunks wait here until the socket is open.
  const pending: Blob[] = []
  let stopRequested = false

  ws.onopen = () => {
    for (const chunk of pending) ws.send(chunk)
    pending.length = 0
    if (stopRequested) ws.send(JSON.stringify({ type: 'stop' }))
  }
  ws.onmessage = (event) => {
    if (typeof event.data !== 'string') return
    try {
      onEvent(JSON.parse(event.data) as LiveDictationEvent)
    } catch {
      /* not ours */
    }
  }
  ws.onclose = (event) => onClose(event.code, CLOSE_MESSAGES[event.code] ?? '')

  return {
    sendAudioChunk(chunk) {
      if (ws.readyState === WebSocket.OPEN) ws.send(chunk)
      else if (ws.readyState === WebSocket.CONNECTING) pending.push(chunk)
    },
    stop() {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' }))
      else if (ws.readyState === WebSocket.CONNECTING) stopRequested = true
    },
    close() {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)
        ws.close()
    },
  }
}
