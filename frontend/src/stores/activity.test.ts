import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { isActive, useActivityStore } from '@/stores/activity'

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
    result_asset_id: null,
    error_message: null,
    created_at: '2026-09-14T10:00:00Z',
    updated_at: '2026-09-14T10:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  useActivityStore.getState().reset()
})

afterEach(() => {
  useActivityStore.getState().reset()
  vi.restoreAllMocks()
})

describe('activity store', () => {
  it('reads every job, not only the running ones', async () => {
    // Filtering to active would make a failed backfill vanish from the indicator at
    // exactly the moment it most needs to be seen.
    const list = vi
      .spyOn(activityApi, 'list')
      .mockResolvedValue([job({ status: 'error', error_message: 'provider says no' })])

    await useActivityStore.getState().refresh()

    expect(list).toHaveBeenCalledWith({ limit: 10 })
    expect(useActivityStore.getState().jobs).toHaveLength(1)
  })

  it('knows which jobs are still running', () => {
    expect(isActive(job({ status: 'queued' }))).toBe(true)
    expect(isActive(job({ status: 'processing' }))).toBe(true)
    expect(isActive(job({ status: 'done' }))).toBe(false)
    expect(isActive(job({ status: 'error' }))).toBe(false)
    expect(isActive(job({ status: 'cancelled' }))).toBe(false)
  })

  it('cancels through the api and reloads', async () => {
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    const cancel = vi
      .spyOn(activityApi, 'cancel')
      .mockResolvedValue(job({ status: 'cancelled' }))

    await useActivityStore.getState().cancel(job())

    expect(cancel).toHaveBeenCalledWith('enrichment', 'j1')
  })

  it('surfaces a failure to read rather than emptying silently', async () => {
    vi.spyOn(activityApi, 'list').mockRejectedValue(new Error('offline'))

    await useActivityStore.getState().refresh()

    expect(useActivityStore.getState().error).toMatch(/offline|activity/i)
  })

  it('discards a response that lands after a reset', async () => {
    // Otherwise signing out repopulates the next person's header with the previous
    // person's jobs, from a request that was already in flight.
    let release: (jobs: ActivityJob[]) => void = () => {}
    vi.spyOn(activityApi, 'list').mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      })
    )

    const pending = useActivityStore.getState().refresh()
    useActivityStore.getState().reset()
    release([job()])
    await pending

    expect(useActivityStore.getState().jobs).toEqual([])
  })
})
