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

  it('reloads the suggestions when an autotag run finishes', async () => {
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

  it('also reloads when a generate-all run finishes', async () => {
    // Autotag can be run on its own or as part of "Generate all" — either one ending
    // has to produce suggestions this panel has not seen yet.
    useActivityStore.setState({
      jobs: [job({ action: 'generate_all', status: 'processing' })],
    })
    const { rerender } = renderPanel()
    await waitFor(() => expect(enrichmentApi.suggestions).toHaveBeenCalledTimes(1))

    useActivityStore.setState({ jobs: [job({ action: 'generate_all', status: 'done' })] })
    rerender(
      <MemoryRouter>
        <SuggestionPanel assetId="a1" />
      </MemoryRouter>
    )

    await waitFor(() => expect(enrichmentApi.suggestions).toHaveBeenCalledTimes(2))
  })

  // ─── accept all ─────────────────────────────────────────────────────────

  it('offers no "accept all" for a single suggestion', async () => {
    vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([suggestion()])
    renderPanel()

    await screen.findByText('DARPA')
    expect(screen.queryByRole('button', { name: /accept all/i })).toBeNull()
  })

  it('accepts every pending suggestion in one click', async () => {
    vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([
      suggestion({ id: 's1', kind: 'title', value: 'Giordano on Neuroweapons' }),
      suggestion({ id: 's2', kind: 'tag', value: 'DARPA' }),
      suggestion({ id: 's3', kind: 'tag', value: 'neuroweapons' }),
    ])
    const accept = vi
      .spyOn(enrichmentApi, 'accept')
      .mockImplementation((_assetId, id) =>
        Promise.resolve(suggestion({ id, status: 'accepted' }))
      )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /accept all/i }))

    await waitFor(() => expect(accept).toHaveBeenCalledTimes(3))
    expect(accept).toHaveBeenCalledWith('a1', 's1')
    expect(accept).toHaveBeenCalledWith('a1', 's2')
    expect(accept).toHaveBeenCalledWith('a1', 's3')
    await waitFor(() =>
      expect(screen.queryByText(/nothing is applied until you say so/i)).toBeNull()
    )
  })
})
