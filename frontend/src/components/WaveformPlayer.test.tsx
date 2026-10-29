import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WaveformPlayer from '@/components/WaveformPlayer'
import { assetsApi, type Asset } from '@/api/assets'
import { noAttribution } from '@/test-fixtures'

// jsdom has no canvas, so wavesurfer cannot draw here. What matters is what it is
// handed: the page's own <audio> element, and always peaks (never left to fetch).
const surfer = vi.hoisted(() => ({
  create: vi.fn(),
  destroy: vi.fn(),
}))
vi.mock('wavesurfer.js', () => ({
  default: {
    create: (options: unknown) => {
      surfer.create(options)
      return { destroy: surfer.destroy }
    },
  },
}))

function audioAsset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Giordano interview',
    description: null,
    summary: null,
    ...noAttribution,
    asset_type: 'audio',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: 'giordano.mp3',
    mime_type: 'audio/mpeg',
    file_format: 'mp3',
    size_bytes: 100,
    duration_seconds: 120,
    width: null,
    height: null,
    codec: 'mp3',
    file_url: '/media/u/a1.mp3?exp=1&sig=x',
    thumb_url: null,
    missing: false,
    tags: [],
    upload_date: '2026-09-13T10:00:00Z',
    modified_date: '2026-09-13T10:00:00Z',
    metadata_modified_date: '2026-09-13T10:00:00Z',
    ...overrides,
  }
}

function renderPlayer(
  asset = audioAsset(),
  attachPlayer = vi.fn(),
  onTimeUpdate = vi.fn()
) {
  const view = render(
    <WaveformPlayer
      asset={asset}
      attachPlayer={attachPlayer}
      onTimeUpdate={onTimeUpdate}
    />
  )
  const audio = view.container.querySelector('audio') as HTMLAudioElement
  return { ...view, audio, attachPlayer, onTimeUpdate }
}

/** What jsdom cannot provide: a duration, as the browser reports it from metadata. */
function loadMetadata(audio: HTMLAudioElement, seconds: number) {
  Object.defineProperty(audio, 'duration', { configurable: true, value: seconds })
  fireEvent.loadedMetadata(audio)
}

function lastCreateOptions() {
  return surfer.create.mock.lastCall?.[0] as {
    media: HTMLMediaElement
    peaks: number[][]
    duration?: number
  }
}

beforeEach(() => {
  surfer.create.mockClear()
  surfer.destroy.mockClear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('WaveformPlayer', () => {
  it('draws the server’s peaks over the page’s own audio element', async () => {
    vi.spyOn(assetsApi, 'peaks').mockResolvedValue({
      duration: 120,
      peaks: [0.2, 1, 0.5],
    })
    const { audio } = renderPlayer()

    await waitFor(() => expect(lastCreateOptions().peaks).toEqual([[0.2, 1, 0.5]]))
    expect(lastCreateOptions().media).toBe(audio)
    expect(lastCreateOptions().duration).toBe(120)
    // The native control is gone; this component is the control now.
    expect(audio).not.toHaveAttribute('controls')
  })

  it('scrubs on a flat line until the peaks arrive, then swaps them in', async () => {
    let resolve: (value: { duration: number; peaks: number[] }) => void = () => {}
    vi.spyOn(assetsApi, 'peaks').mockReturnValue(new Promise((r) => (resolve = r)))
    renderPlayer()

    // Never created without peaks: that is what would make it download the whole file.
    expect(lastCreateOptions().peaks).toEqual([[0]])

    await act(async () => resolve({ duration: 120, peaks: [0.4, 1] }))

    expect(surfer.destroy).toHaveBeenCalledTimes(1)
    expect(lastCreateOptions().peaks).toEqual([[0.4, 1]])
  })

  it('keeps the flat line when the waveform cannot be had', async () => {
    const peaks = vi.spyOn(assetsApi, 'peaks').mockRejectedValue(new Error('422'))
    renderPlayer()

    await waitFor(() => expect(peaks).toHaveBeenCalled())
    expect(surfer.create).toHaveBeenCalledTimes(1)
    expect(lastCreateOptions().peaks).toEqual([[0]])
    expect(screen.getByRole('slider', { name: 'Seek' })).toBeInTheDocument()
  })

  it('hands the element to the panel, so start-at and transcript seeks still work', () => {
    vi.spyOn(assetsApi, 'peaks').mockReturnValue(new Promise(() => {}))
    const { audio, attachPlayer } = renderPlayer()

    expect(attachPlayer).toHaveBeenCalledWith(audio)
  })

  it('plays and pauses the element', async () => {
    vi.spyOn(assetsApi, 'peaks').mockReturnValue(new Promise(() => {}))
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const pause = vi
      .spyOn(HTMLMediaElement.prototype, 'pause')
      .mockImplementation(() => {})
    const user = userEvent.setup()
    const { audio } = renderPlayer()

    await user.click(screen.getByRole('button', { name: 'Play' }))
    expect(play).toHaveBeenCalledTimes(1)

    // The button follows the element, not its own clicks: anything else may start it.
    Object.defineProperty(audio, 'paused', { configurable: true, value: false })
    fireEvent.play(audio)
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    expect(pause).toHaveBeenCalledTimes(1)
  })

  it('seeks with the arrow keys, within the recording', () => {
    vi.spyOn(assetsApi, 'peaks').mockReturnValue(new Promise(() => {}))
    const { audio } = renderPlayer()
    loadMetadata(audio, 8)
    const slider = screen.getByRole('slider', { name: 'Seek' })

    fireEvent.keyDown(slider, { key: 'ArrowRight' })
    expect(audio.currentTime).toBe(5)
    fireEvent.keyDown(slider, { key: 'ArrowRight' })
    expect(audio.currentTime).toBe(8)
    fireEvent.keyDown(slider, { key: 'ArrowLeft' })
    expect(audio.currentTime).toBe(3)
  })

  it('passes time updates on, so the clip’s out-point pause still runs', () => {
    vi.spyOn(assetsApi, 'peaks').mockReturnValue(new Promise(() => {}))
    const { audio, onTimeUpdate } = renderPlayer()
    loadMetadata(audio, 120)

    audio.currentTime = 65
    fireEvent.timeUpdate(audio)

    expect(onTimeUpdate).toHaveBeenCalledTimes(1)
    expect(screen.getByText('1:05 / 2:00')).toBeInTheDocument()
  })

  it('dims the parent’s waveform outside a clip’s window', () => {
    vi.spyOn(assetsApi, 'peaks').mockReturnValue(new Promise(() => {}))
    const { audio, container } = renderPlayer(
      audioAsset({ source: 'clip', parent_asset_id: 'p1', in_point: 30, out_point: 90 })
    )
    loadMetadata(audio, 120)

    const shades = container.querySelectorAll<HTMLElement>('.bg-gray-900\\/70')
    expect([...shades].map((s) => s.style.width)).toEqual(['25%', '25%'])
  })
})
