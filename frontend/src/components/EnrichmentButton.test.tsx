import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { FileText } from 'lucide-react'
import { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import EnrichmentButton from '@/components/EnrichmentButton'
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

function renderButton(overrides: Partial<Parameters<typeof EnrichmentButton>[0]> = {}) {
  const props = {
    assetId: 'a1',
    action: 'summarize',
    icon: FileText,
    label: 'Summarise with AI',
    runningLabel: 'Summarising…',
    start: enrichmentApi.summarize,
    failureMessage: 'Could not start summarising',
    ...overrides,
  }
  return render(
    <MemoryRouter>
      <EnrichmentButton {...props} />
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

describe('EnrichmentButton', () => {
  it('starts the job it was given for this asset', async () => {
    const summarize = vi.spyOn(enrichmentApi, 'summarize').mockResolvedValue(job())
    renderButton({ start: enrichmentApi.summarize })

    await userEvent.click(screen.getByRole('button', { name: /summarise with ai/i }))

    await waitFor(() => expect(summarize).toHaveBeenCalledWith('a1'))
  })

  it('drives the describe job just as well', async () => {
    const describeCall = vi.spyOn(enrichmentApi, 'describe').mockResolvedValue(job())
    renderButton({
      action: 'describe',
      label: 'Describe with AI',
      start: enrichmentApi.describe,
    })

    await userEvent.click(screen.getByRole('button', { name: /describe with ai/i }))

    await waitFor(() => expect(describeCall).toHaveBeenCalledWith('a1'))
  })

  it('shows progress from the store rather than opening its own poll', () => {
    useActivityStore.setState({ jobs: [job({ stage: 'Summarising' })] })
    renderButton()

    const button = screen.getByRole('button')
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent(/summarising/i)
  })

  it('ignores a different action on the same asset', () => {
    // Describing must not disable the summarise button, and vice versa.
    useActivityStore.setState({ jobs: [job({ action: 'describe' })] })
    renderButton({ action: 'summarize' })

    expect(screen.getByRole('button')).toBeEnabled()
  })

  it('ignores the same action on another asset', () => {
    useActivityStore.setState({ jobs: [job({ asset_id: 'somebody-else' })] })
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
    // Where "you wrote this yourself" and a provider refusal both arrive.
    useActivityStore.setState({
      jobs: [job({ status: 'error', error_message: 'credit balance is too low' })],
    })
    renderButton()

    expect(screen.getByText(/credit balance is too low/i)).toBeInTheDocument()
  })

  it('re-reads the asset when the job finishes', async () => {
    const get = vi.spyOn(assetsApi, 'get').mockResolvedValue({ id: 'a1' } as never)
    useActivityStore.setState({ jobs: [job({ status: 'processing' })] })
    const { rerender } = renderButton()

    useActivityStore.setState({ jobs: [job({ status: 'done' })] })
    rerender(
      <MemoryRouter>
        <EnrichmentButton
          assetId="a1"
          action="summarize"
          icon={FileText}
          label="Summarise with AI"
          runningLabel="Summarising…"
          start={enrichmentApi.summarize}
          failureMessage="Could not start summarising"
        />
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
