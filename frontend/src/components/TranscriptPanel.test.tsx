import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TranscriptPanel from '@/components/TranscriptPanel'
import { activityApi, transcriptsApi, type Transcript } from '@/api/transcripts'

function makeTranscript(overrides: Partial<Transcript> = {}): Transcript {
  return {
    asset_id: 'a1',
    status: 'done',
    model: 'nova-3',
    language: 'en',
    segments: [
      {
        id: 's1',
        idx: 0,
        text: 'We are looking at deploying nano weapons via aerosol dispersion.',
        start_time: 412,
        end_time: 419.2,
        speaker: 0,
        edited: false,
        words: [],
      },
      {
        id: 's2',
        idx: 1,
        text: 'It is already feasible.',
        start_time: 425,
        end_time: 427,
        speaker: 1,
        edited: false,
        words: [],
      },
    ],
    ...overrides,
  }
}

beforeEach(() => {
  vi.spyOn(activityApi, 'list').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('TranscriptPanel', () => {
  it('renders segments with jumpable timestamps', async () => {
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript())

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    // 412 seconds renders as 6:52 — hours omitted because there are none.
    expect(await screen.findByRole('button', { name: '6:52' })).toBeInTheDocument()
    expect(screen.getByText(/deploying nano weapons/)).toBeInTheDocument()
  })

  it('seeks the player when a timestamp is clicked', async () => {
    // The payoff of the whole milestone: find the moment, jump to it.
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript())
    const onSeek = vi.fn()

    render(<TranscriptPanel assetId="a1" onSeek={onSeek} currentTime={0} />)
    await userEvent.click(await screen.findByRole('button', { name: '6:52' }))

    expect(onSeek).toHaveBeenCalledWith(412)
  })

  it('labels speakers so an interview can be followed', async () => {
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript())

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    expect(await screen.findByText('S1:')).toBeInTheDocument()
    expect(screen.getByText('S2:')).toBeInTheDocument()
  })

  it('invites transcription when there is none', async () => {
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(
      makeTranscript({ segments: [], status: null, model: null })
    )

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    expect(await screen.findByRole('button', { name: /transcribe/i })).toBeInTheDocument()
    expect(screen.getByText(/no transcript yet/i)).toBeInTheDocument()
  })

  it('offers a re-run once a transcript exists', async () => {
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript())

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    expect(
      await screen.findByRole('button', { name: /re-transcribe/i })
    ).toBeInTheDocument()
  })

  it('shows progress and a cancel button while a job runs', async () => {
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(
      makeTranscript({ segments: [], status: 'running' })
    )
    vi.spyOn(activityApi, 'list').mockResolvedValue([
      {
        id: 'j1',
        kind: 'enrichment',
        action: 'transcribe',
        status: 'processing',
        stalled: false,
        stage: 'Transcribing',
        progress: 40,
        detail: '',
        asset_id: 'a1',
        asset_name: 'Interview',
        model: 'nova-3',
        result_asset_id: null,
        error_message: null,
        created_at: '2026-09-13T07:00:00Z',
        updated_at: '2026-09-13T07:00:00Z',
      },
    ])

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    expect(await screen.findByText(/Transcribing 40%/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
  })

  it('ignores the embed job that transcription queues behind itself', async () => {
    // Regression: `activityApi.list` returns active jobs of every action for the asset,
    // newest first, and a successful transcription immediately queues an embed job. The
    // panel used to take jobs[0], so it showed the embed job's progress under the
    // transcription heading and its Cancel button stopped the embedding instead.
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript({ status: 'done' }))
    vi.spyOn(activityApi, 'list').mockResolvedValue([
      {
        id: 'j2',
        kind: 'enrichment',
        action: 'embed',
        status: 'processing',
        stalled: false,
        stage: 'Embedding transcript',
        progress: 40,
        detail: '',
        asset_id: 'a1',
        asset_name: 'Interview',
        model: 'text-embedding-3-small',
        result_asset_id: null,
        error_message: null,
        created_at: '2026-09-13T08:00:00Z',
        updated_at: '2026-09-13T08:00:00Z',
      },
    ])

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    expect(await screen.findByText(/deploying nano weapons/i)).toBeInTheDocument()
    expect(screen.queryByText(/Embedding transcript/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument()
  })

  it('says so when a job stopped responding rather than claiming it is running', async () => {
    // The sweeper will fail it within the minute; until then the UI must not lie.
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript({ segments: [] }))
    vi.spyOn(activityApi, 'list').mockResolvedValue([
      {
        id: 'j1',
        kind: 'enrichment',
        action: 'transcribe',
        status: 'processing',
        stalled: true,
        stage: 'Transcribing',
        progress: 40,
        detail: '',
        asset_id: 'a1',
        asset_name: 'Interview',
        model: 'nova-3',
        result_asset_id: null,
        error_message: null,
        created_at: '2026-09-13T07:00:00Z',
        updated_at: '2026-09-13T07:00:00Z',
      },
    ])

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    expect(await screen.findByText(/not responding/i)).toBeInTheDocument()
  })

  it('marks a hand-corrected segment so a re-run is known to preserve it', async () => {
    const transcript = makeTranscript()
    transcript.segments[0].edited = true
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(transcript)

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    expect(await screen.findByText('(edited)')).toBeInTheDocument()
  })

  it('saves a correction', async () => {
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript())
    const update = vi.spyOn(transcriptsApi, 'updateSegment').mockResolvedValue({
      id: 's1',
      idx: 0,
      text: 'Corrected wording.',
      start_time: 412,
      end_time: 419.2,
      speaker: 0,
      edited: true,
      words: [],
    })

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    await userEvent.click((await screen.findAllByLabelText('Edit this segment'))[0])
    const box = await screen.findByRole('textbox')
    await userEvent.clear(box)
    await userEvent.type(box, 'Corrected wording.{Enter}')

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith('a1', 's1', { text: 'Corrected wording.' })
    })
  })

  it('reports a failed job rather than showing an empty transcript', async () => {
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript({ segments: [] }))
    vi.spyOn(activityApi, 'list').mockResolvedValue([
      {
        id: 'j1',
        kind: 'enrichment',
        action: 'transcribe',
        status: 'error',
        stalled: false,
        stage: '',
        progress: 0,
        detail: '',
        asset_id: 'a1',
        asset_name: 'Interview',
        model: '',
        result_asset_id: null,
        error_message:
          'No Deepgram API key is configured. Add one in Settings to transcribe.',
        created_at: '2026-09-13T07:00:00Z',
        updated_at: '2026-09-13T07:00:00Z',
      },
    ])

    render(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />)

    expect(
      await screen.findByText(/No Deepgram API key is configured/)
    ).toBeInTheDocument()
  })

  it('highlights the segment currently being spoken', async () => {
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue(makeTranscript())

    const { container, rerender } = render(
      <TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={0} />
    )
    await screen.findByText(/deploying nano weapons/)

    await act(async () => {
      rerender(<TranscriptPanel assetId="a1" onSeek={vi.fn()} currentTime={415} />)
    })

    const highlighted = container.querySelectorAll(
      'li.bg-blue-50, li[class*="bg-blue-50"]'
    )
    expect(highlighted.length).toBe(1)
  })
})
