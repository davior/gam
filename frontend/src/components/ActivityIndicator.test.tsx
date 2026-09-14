import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ActivityIndicator from '@/components/ActivityIndicator'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { useActivityStore } from '@/stores/activity'

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
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
    error_message: null,
    created_at: '2026-09-14T10:00:00Z',
    updated_at: '2026-09-14T10:00:00Z',
    ...overrides,
  }
}

function renderWith(jobs: ActivityJob[]) {
  vi.spyOn(activityApi, 'list').mockResolvedValue(jobs)
  useActivityStore.setState({ jobs })
  return render(
    <MemoryRouter>
      <ActivityIndicator />
    </MemoryRouter>
  )
}

afterEach(() => {
  useActivityStore.getState().reset()
  vi.restoreAllMocks()
})

describe('ActivityIndicator', () => {
  it('shows nothing at all when nothing has ever run', () => {
    const { container } = renderWith([])
    expect(container).toBeEmptyDOMElement()
  })

  it('counts what is running', async () => {
    renderWith([job(), job({ id: 'j2', action: 'embed' })])
    expect(
      await screen.findByRole('button', { name: /2 jobs running/i })
    ).toBeInTheDocument()
  })

  it('names a whole-library job, which has no asset', async () => {
    // A backfill belongs to no asset, so asset_name is empty and would render blank.
    renderWith([job({ action: 'backfill_embeddings', asset_id: null, asset_name: '' })])

    await userEvent.click(screen.getByRole('button', { name: /1 job running/i }))

    expect(screen.getByText('Embedding the library')).toBeInTheDocument()
    expect(screen.getByText('Your whole library')).toBeInTheDocument()
  })

  it('keeps a failed job visible with its reason', async () => {
    renderWith([job({ status: 'error', error_message: 'provider says no' })])

    await userEvent.click(screen.getByRole('button', { name: /background activity/i }))

    expect(screen.getByText('provider says no')).toBeInTheDocument()
  })

  it('cancels a running job', async () => {
    const cancel = vi
      .spyOn(activityApi, 'cancel')
      .mockResolvedValue(job({ status: 'cancelled' }))
    renderWith([job()])

    await userEvent.click(screen.getByRole('button', { name: /1 job running/i }))
    await userEvent.click(screen.getByRole('button', { name: /cancel transcribing/i }))

    expect(cancel).toHaveBeenCalledWith('enrichment', 'j1')
  })

  it('does not offer cancel on a job that already finished', async () => {
    renderWith([job({ status: 'done' })])

    await userEvent.click(screen.getByRole('button', { name: /background activity/i }))

    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument()
  })
})
