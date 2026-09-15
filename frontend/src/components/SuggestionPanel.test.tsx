import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SuggestionPanel from '@/components/SuggestionPanel'
import { enrichmentApi, type Suggestion } from '@/api/enrichment'
import { assetsApi } from '@/api/assets'
import { useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'
import type { ActivityJob } from '@/api/transcripts'

function suggestion(overrides: Partial<Suggestion> = {}): Suggestion {
  return {
    id: 's1',
    asset_id: 'a1',
    kind: 'tag',
    value: 'DARPA',
    status: 'pending',
    ...overrides,
  }
}

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    kind: 'enrichment',
    action: 'autotag',
    status: 'processing',
    stalled: false,
    stage: 'Choosing tags',
    progress: 30,
    detail: '',
    asset_id: 'a1',
    asset_name: 'clip.mp4',
    model: 'claude-sonnet-4-20250514',
    error_message: null,
    created_at: '2026-09-15T06:00:00Z',
    updated_at: '2026-09-15T06:00:00Z',
    ...overrides,
  }
}

function codedError(code: string) {
  const error = new AxiosError('failed')
  // @ts-expect-error - a minimal response is all apiErrorCode reads
  error.response = { data: { detail: { code, message: 'nope' } } }
  return error
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <SuggestionPanel assetId="a1" />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useActivityStore.getState().reset()
  useLibraryStore.getState().reset()
  vi.spyOn(useActivityStore.getState(), 'refresh').mockResolvedValue(undefined)
  vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([])
  vi.spyOn(assetsApi, 'get').mockResolvedValue({ id: 'a1' } as never)
})

afterEach(() => {
  useActivityStore.getState().reset()
  useLibraryStore.getState().reset()
  vi.restoreAllMocks()
})

describe('SuggestionPanel', () => {
  it('starts a run for this asset', async () => {
    const autotag = vi.spyOn(enrichmentApi, 'autotag').mockResolvedValue(job())
    renderPanel()

    await userEvent.click(
      screen.getByRole('button', { name: /suggest tags and a title/i })
    )

    await waitFor(() => expect(autotag).toHaveBeenCalledWith('a1'))
  })

  it('shows nothing when there is nothing pending', async () => {
    renderPanel()

    await waitFor(() => expect(enrichmentApi.suggestions).toHaveBeenCalled())
    expect(screen.queryByText(/nothing is applied until you say so/i)).toBeNull()
  })

  it('says plainly that nothing has been applied', async () => {
    vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([suggestion()])
    renderPanel()

    expect(
      await screen.findByText(/nothing is applied until you say so/i)
    ).toBeInTheDocument()
  })

  it('separates a title suggestion from the tags', async () => {
    vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([
      suggestion({ id: 's1', kind: 'title', value: 'Giordano on Neuroweapons' }),
      suggestion({ id: 's2', kind: 'tag', value: 'DARPA' }),
    ])
    renderPanel()

    expect(await screen.findByText(/rename to/i)).toBeInTheDocument()
    expect(screen.getByText('Giordano on Neuroweapons')).toBeInTheDocument()
    expect(screen.getByText('DARPA')).toBeInTheDocument()
  })

  it('accepts one and re-reads the asset', async () => {
    // Accepting writes to the library — a tag attached or the asset renamed — so what
    // is on screen behind this panel is stale until the asset comes back.
    vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([suggestion()])
    const accept = vi
      .spyOn(enrichmentApi, 'accept')
      .mockResolvedValue(suggestion({ status: 'accepted' }))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /accept darpa/i }))

    await waitFor(() => expect(accept).toHaveBeenCalledWith('a1', 's1'))
    expect(assetsApi.get).toHaveBeenCalledWith('a1')
    await waitFor(() => expect(screen.queryByText('DARPA')).toBeNull())
  })

  it('rejects one without touching the asset', async () => {
    vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([suggestion()])
    const reject = vi
      .spyOn(enrichmentApi, 'reject')
      .mockResolvedValue(suggestion({ status: 'rejected' }))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /dismiss darpa/i }))

    await waitFor(() => expect(reject).toHaveBeenCalledWith('a1', 's1'))
    expect(assetsApi.get).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByText('DARPA')).toBeNull())
  })

  it('keeps the suggestion on screen when the decision fails', async () => {
    vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([suggestion()])
    vi.spyOn(enrichmentApi, 'accept').mockRejectedValue(codedError('boom'))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /accept darpa/i }))

    expect(await screen.findByText(/nope/i)).toBeInTheDocument()
    expect(screen.getByText('DARPA')).toBeInTheDocument()
  })

  it('shows progress from the store rather than polling', async () => {
    useActivityStore.setState({ jobs: [job()] })
    renderPanel()

    const button = screen.getByRole('button', { name: /choosing tags/i })
    expect(button).toBeDisabled()
  })

  it('reloads the suggestions when a run finishes', async () => {
    useActivityStore.setState({ jobs: [job({ status: 'processing' })] })
    const { rerender } = renderPanel()
    await waitFor(() => expect(enrichmentApi.suggestions).toHaveBeenCalledTimes(1))

    useActivityStore.setState({ jobs: [job({ status: 'done' })] })
    rerender(
      <MemoryRouter>
        <SuggestionPanel assetId="a1" />
      </MemoryRouter>
    )

    await waitFor(() => expect(enrichmentApi.suggestions).toHaveBeenCalledTimes(2))
  })

  it('points at Settings when no provider is configured', async () => {
    vi.spyOn(enrichmentApi, 'autotag').mockRejectedValue(
      codedError('provider_unavailable')
    )
    renderPanel()

    await userEvent.click(screen.getByRole('button', { name: /suggest tags/i }))

    expect(
      await screen.findByRole('link', { name: /add one in settings/i })
    ).toBeInTheDocument()
  })

  it('surfaces what a failed run said', async () => {
    useActivityStore.setState({
      jobs: [job({ status: 'error', error_message: 'The provider suggested nothing' })],
    })
    renderPanel()

    expect(screen.getByText(/the provider suggested nothing/i)).toBeInTheDocument()
  })
})
