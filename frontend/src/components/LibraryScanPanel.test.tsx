import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LibraryScanPanel from '@/components/LibraryScanPanel'
import { assetsApi } from '@/api/assets'
import type { ActivityJob } from '@/api/transcripts'
import { useActivityStore } from '@/stores/activity'

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    kind: 'enrichment',
    action: 'analyze_music_library',
    status: 'processing',
    stalled: false,
    stage: 'Analysing music',
    progress: 30,
    detail: '',
    asset_id: null,
    asset_name: '',
    model: '',
    result_asset_id: null,
    result_asset_ids: [],
    error_message: null,
    created_at: '2026-10-26T00:00:00Z',
    updated_at: '2026-10-26T00:00:00Z',
    ...overrides,
  }
}

const refresh = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  refresh.mockClear()
  useActivityStore.setState({ jobs: [], refresh })
})

afterEach(() => vi.restoreAllMocks())

describe('LibraryScanPanel', () => {
  it('starts a library-wide music analysis', async () => {
    const start = vi.spyOn(assetsApi, 'analyzeMusicLibrary').mockResolvedValue(job())
    render(<LibraryScanPanel />)

    await userEvent.click(screen.getByRole('button', { name: 'Analyse' }))

    expect(start).toHaveBeenCalled()
    expect(refresh).toHaveBeenCalled()
  })

  it('gives the tag harvest a button too', async () => {
    const start = vi
      .spyOn(assetsApi, 'harvestAttribution')
      .mockResolvedValue(job({ action: 'harvest_attribution' }))
    render(<LibraryScanPanel />)

    await userEvent.click(screen.getByRole('button', { name: 'Read tags' }))
    expect(start).toHaveBeenCalled()
  })

  it('cannot be started twice while it runs', () => {
    useActivityStore.setState({ jobs: [job()] })
    render(<LibraryScanPanel />)

    expect(screen.getByRole('button', { name: /Running/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Read tags' })).toBeEnabled()
  })

  it('says what the last run did', () => {
    useActivityStore.setState({
      jobs: [job({ status: 'done', detail: '12 filled in of 14 analysed' })],
    })
    render(<LibraryScanPanel />)
    expect(screen.getByText('Last run: 12 filled in of 14 analysed')).toBeInTheDocument()
  })
})
