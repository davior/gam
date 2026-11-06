import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as configModule from '@/api/config'
import { assistantApi } from '@/api/assistant'
import type { LiveDictationEvent } from '@/api/suiteSpeech'
import AssistantPanel from '@/components/AssistantPanel'
import { useAssistantStore } from '@/stores/assistant'

/**
 * Dictation into the assistant's message box. The suite's stream is faked at the module
 * boundary; the browser's microphone and recorder are faked as globals.
 */

const stream = vi.hoisted(() => ({
  ready: { state: 'ready' } as
    { state: 'ready' } | { state: 'local' } | { state: 'unavailable'; reason: string },
  onEvent: null as null | ((e: LiveDictationEvent) => void),
  onClose: null as null | ((code: number, reason: string) => void),
  stopped: 0,
  chunks: 0,
}))

vi.mock('@/api/suiteSpeech', () => ({
  suiteSpeechApi: { ready: () => Promise.resolve(stream.ready) },
  connectLiveDictation: (
    onEvent: (e: LiveDictationEvent) => void,
    onClose: (code: number, reason: string) => void
  ) => {
    stream.onEvent = onEvent
    stream.onClose = onClose
    return {
      sendAudioChunk: () => {
        stream.chunks += 1
      },
      stop: () => {
        stream.stopped += 1
        onClose(1000, '')
      },
      close: () => undefined,
    }
  },
}))

class FakeRecorder {
  static isTypeSupported = () => true
  state = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  start() {
    this.state = 'recording'
  }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['last']) })
    this.onstop?.()
  }
}

async function open(ready = true) {
  vi.spyOn(assistantApi, 'status').mockResolvedValue({
    available: true,
    ready,
    model: 'Claude',
    message: null,
  })
  vi.spyOn(assistantApi, 'personas').mockResolvedValue([])
  vi.spyOn(assistantApi, 'listSessions').mockResolvedValue([])
  render(
    <MemoryRouter>
      <AssistantPanel />
    </MemoryRouter>
  )
  await act(async () => {
    useAssistantStore.getState().setOpen(true)
  })
}

beforeEach(() => {
  Object.assign(stream, {
    ready: { state: 'ready' },
    onEvent: null,
    onClose: null,
    stopped: 0,
    chunks: 0,
  })
  vi.stubGlobal('MediaRecorder', FakeRecorder)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: () =>
        Promise.resolve({ getTracks: () => [{ stop: () => undefined }] }),
    },
  })
  vi.spyOn(configModule, 'loadConfig').mockResolvedValue({
    notes_base_url: 'https://notes.example.test',
    read_only: false,
    ai_backend: 'suite',
    suite_base_url: 'https://suite.example.test/',
  })
  useAssistantStore.getState().reset()
})

afterEach(() => {
  cleanup()
  useAssistantStore.getState().reset()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('dictating into the assistant', () => {
  it('shows words as they are heard and adds each phrase to what is typed, without sending', async () => {
    const ask = vi.spyOn(assistantApi, 'ask')
    await open()
    const box = await screen.findByLabelText('Message')
    await userEvent.type(box, 'Find the clips')

    await userEvent.click(await screen.findByRole('button', { name: 'Dictate' }))
    expect(screen.getByRole('button', { name: 'Stop dictating' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )

    act(() => stream.onEvent?.({ type: 'interim', text: 'about the' }))
    expect(screen.getByText('about the…')).toBeInTheDocument()
    act(() => stream.onEvent?.({ type: 'final', text: 'about the river' }))

    expect(box).toHaveValue('Find the clips about the river')
    expect(screen.queryByText('about the…')).not.toBeInTheDocument()
    expect(ask).not.toHaveBeenCalled()

    // Stopping sends the recorder's last chunk first, then asks the suite to flush.
    await userEvent.click(screen.getByRole('button', { name: 'Stop dictating' }))
    expect(stream.chunks).toBe(1)
    expect(stream.stopped).toBe(1)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Dictate' })).toBeInTheDocument()
    )
  })

  it('says why when the connection ends without being asked to', async () => {
    await open()
    await userEvent.click(await screen.findByRole('button', { name: 'Dictate' }))
    act(() =>
      stream.onClose?.(
        4400,
        'No Deepgram key is set up in Gecko Suite, so dictation is off.'
      )
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('No Deepgram key')
  })

  it('shows a greyed microphone that says why when the suite cannot transcribe', async () => {
    // A microphone that is simply not there gives nothing to go on.
    stream.ready = {
      state: 'unavailable',
      reason: 'No Deepgram key is set up in Gecko Suite.',
    }
    await open()
    const mic = await screen.findByRole('button', { name: 'Dictate (unavailable)' })
    expect(mic).toHaveAttribute('title', 'No Deepgram key is set up in Gecko Suite.')
    await userEvent.click(mic)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Dictation is off: No Deepgram key'
    )
  })

  it('offers no microphone when AI does not run through the suite', async () => {
    stream.ready = { state: 'local' }
    await open()
    await screen.findByLabelText('Message')
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Dictate/ })).not.toBeInTheDocument()
    )
  })

  it('offers no microphone while the assistant itself is not available', async () => {
    await open(false)
    await screen.findByLabelText('Message')
    expect(screen.queryByRole('button', { name: /Dictate/ })).not.toBeInTheDocument()
  })
})
