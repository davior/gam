import { create } from 'zustand'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { apiErrorMessage } from '@/api/client'

/**
 * Everything currently happening, for the whole user rather than one asset.
 *
 * Before this there was no such view: `/api/activity` was polled only inside the
 * transcript panel, scoped to the open asset, so closing the modal lost sight of a
 * running job and a backfill — which belongs to no asset at all — had nowhere to appear.
 */

const ACTIVE_STATUSES = ['queued', 'processing']

/** Fast enough to feel live while something runs. */
const BUSY_MS = 2000
/** Slow enough to be free when nothing does. */
const IDLE_MS = 10000

export function isActive(job: ActivityJob): boolean {
  return ACTIVE_STATUSES.includes(job.status)
}

interface ActivityState {
  jobs: ActivityJob[]
  error: string | null
  start: () => void
  stop: () => void
  refresh: () => Promise<void>
  cancel: (job: ActivityJob) => Promise<void>
  reset: () => void
}

let timer: ReturnType<typeof setTimeout> | null = null
// Bumped by every reset and stop, so a response that lands after one is discarded
// rather than repopulating a store that was deliberately emptied.
let token = 0

export const useActivityStore = create<ActivityState>((set, get) => ({
  jobs: [],
  error: null,

  start() {
    // Idempotent, and deliberately not stopped on unmount. App.tsx wraps each route in
    // its own AppShell, so the indicator remounts on every navigation — stopping on
    // unmount would restart the poll and blank the badge on each route change. It looks
    // like a leak and is the opposite: one timer for the life of the tab.
    if (timer) return
    void get().refresh()
  },

  stop() {
    token += 1
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  },

  async refresh() {
    const mine = ++token
    try {
      // Deliberately unfiltered rather than `{ active: true }`. A backfill that fails
      // would otherwise vanish from the indicator at the moment it most needs to be
      // seen, and the user would never learn it stopped.
      const jobs = await activityApi.list({ limit: 10 })
      if (mine !== token) return
      set({ jobs, error: null })
    } catch (error) {
      if (mine !== token) return
      set({ error: apiErrorMessage(error, 'Could not read background activity') })
    }

    if (mine !== token) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(
      () => {
        void get().refresh()
      },
      get().jobs.some(isActive) ? BUSY_MS : IDLE_MS
    )
  },

  async cancel(job) {
    try {
      await activityApi.cancel(job.kind, job.id)
      await get().refresh()
    } catch (error) {
      set({ error: apiErrorMessage(error, 'Could not cancel that job') })
    }
  },

  reset() {
    get().stop()
    set({ jobs: [], error: null })
  },
}))
