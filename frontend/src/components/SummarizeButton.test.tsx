import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SummarizeButton from '@/components/SummarizeButton'
import { enrichmentApi } from '@/api/enrichment'
import { assetsApi } from '@/api/assets'
import { useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'
import type { ActivityJob } from '@/api/transcripts'

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    kind: 'enrichment',
    action: 'summarize',
    status: 'processing',
    stalled: false,
    stage: 'Summarising',
    progress: 30,
    detail: '',
    asset_id: 'a1',
    asset_name: 'clip.mp4',
    model: 'claude-sonnet-4-20250514',
    error_message: null,
    created_at: '2026-09-15T05:00:00Z',
    updated_at: '2026-09-15T05:00:00Z',
    ...overrides,
  }
}

function codedError(code: string) {
  const error = new AxiosError('failed')
  // @ts-expect-error - a minimal response is all apiErrorCode reads
  error.response = { data: { detail: { code, message: 'nope' } } }
  return error
}

function renderButton() {
  return render(
    <MemoryRouter>
      <SummarizeButton assetId="a1" />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useActivityStore.getState().reset()
  useLibraryStore.getState().reset()
  vi.spyOn(useActivityStore.getState(), 'refresh').mockResolvedValue(undefined)
})

afterEach(() => {
  useActivityStore.getState().reset()
  useLibraryStore.getState().reset()
  vi.restoreAllMocks()
})

describe('SummarizeButton', () => {
  it('starts a summary for this asset', async () => {
    const summarize = vi.spyOn(enrichmentApi, 'summarize').mockResolvedValue(job())
    renderButton()

    await userEvent.click(screen.getByRole('button', { name: /summarise with ai/i }))

    await waitFor(() => expect(summarize).toHaveBeenCalledWith('a1'))
  })

  it('shows progress from the store rather than opening its own poll', async () => {
    useActivityStore.setState({ jobs: [job({ stage: 'Summarising' })] })
    renderButton()

    const button = screen.getByRole('button')
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent(/summarising/i)
  })

  it('ignores another asset‘s job', () => {
    useActivityStore.setState({ jobs: [job({ asset_id: 'someone-else' })] })
    renderButton()

    expect(screen.getByRole('button')).toBeEnabled()
  })

  it('ignores a different action on the same asset', () => {
    // The asset embeds itself after transcription, and that job must not disable this.
    useActivityStore.setState({ jobs: [job({ action: 'embed' })] })
    renderButton()

    expect(screen.getByRole('button')).toBeEnabled()
  })

  it('points at Settings when no provider is configured', async () => {
    vi.spyOn(enrichmentApi, 'summarize').mockRejectedValue(
      codedError('provider_unavailable')
    )
    renderButton()

    await userEvent.click(screen.getByRole('button'))

    expect(
      await screen.findByRole('link', { name: /add one in settings/i })
    ).toBeInTheDocument()
  })

  it('shows any other failure as an error', async () => {
    vi.spyOn(enrichmentApi, 'summarize').mockRejectedValue(codedError('not_summarisable'))
    renderButton()

    await userEvent.click(screen.getByRole('button'))

    expect(await screen.findByText(/nope/i)).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('surfaces what a failed job said', async () => {
    // Where "you wrote this summary yourself" and a provider refusal both arrive.
    useActivityStore.setState({
      jobs: [job({ status: 'error', error_message: 'credit balance is too low' })],
    })
    renderButton()

    expect(screen.getByText(/credit balance is too low/i)).toBeInTheDocument()
  })

  it('re-reads the asset when the job finishes', async () => {
    // The job writes the summary server-side, so the store is stale the moment it ends.
    const get = vi.spyOn(assetsApi, 'get').mockResolvedValue({ id: 'a1' } as never)
    useActivityStore.setState({ jobs: [job({ status: 'processing' })] })
    const { rerender } = renderButton()

    useActivityStore.setState({ jobs: [job({ status: 'done' })] })
    rerender(
      <MemoryRouter>
        <SummarizeButton assetId="a1" />
      </MemoryRouter>
    )

    await waitFor(() => expect(get).toHaveBeenCalledWith('a1'))
  })

  it('does not re-read on first render when nothing was running', async () => {
    const get = vi.spyOn(assetsApi, 'get').mockResolvedValue({ id: 'a1' } as never)
    renderButton()

    await waitFor(() => expect(screen.getByRole('button')).toBeEnabled())
    expect(get).not.toHaveBeenCalled()
  })
})
