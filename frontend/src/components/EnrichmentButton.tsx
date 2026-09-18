import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import type { ActivityJob } from '@/api/transcripts'
import { isActive, useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'

/**
 * One "run this enrichment on this asset" button.
 *
 * Summarise and describe were the same thirty lines twice over — a third copy for the
 * next job would have been the point at which one of them quietly drifted. Owns no
 * timer: the activity store is already polling, so this reads its own asset's job out
 * of that rather than opening a second loop per open modal.
 */

interface Props {
  assetId: string
  /** The job's `action`, which is how its row is found in the activity store. */
  action: string
  icon: LucideIcon
  label: string
  runningLabel: string
  start: (assetId: string) => Promise<ActivityJob>
  failureMessage: string
  /** Re-read the asset when the run ends, for jobs that write a field on it. */
  refreshOnFinish?: boolean
  /** Called once when a running job reaches a terminal state, with that job — for a
   *  caller that needs more than "re-read this same asset" (M7's sub-video
   *  extraction reads `result_asset_id` off it to point at what was created). */
  onFinish?: (job: ActivityJob) => void
  /**
   * A small square icon button beside a field's own label, instead of a full-width
   * row with its own text. The label still names the action, as the accessible name
   * and the running/error tooltip — it just is not painted on screen, because the
   * field label already sits right next to it.
   */
  iconOnly?: boolean
}

export default function EnrichmentButton({
  assetId,
  action,
  icon: Icon,
  label,
  runningLabel,
  start,
  failureMessage,
  refreshOnFinish = true,
  onFinish,
  iconOnly = false,
}: Props) {
  const job = useActivityStore((s) =>
    s.jobs.find((j) => j.asset_id === assetId && j.action === action)
  )
  const refresh = useActivityStore((s) => s.refresh)
  const refreshAsset = useLibraryStore((s) => s.refreshAsset)
  const [error, setError] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  const running = job ? isActive(job) : false

  // The job writes to the asset server-side, so the store is stale the moment it
  // finishes. Re-read once on the running -> finished edge; without this the panel keeps
  // showing the old value until the library is reloaded.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) {
      if (refreshOnFinish) void refreshAsset(assetId)
      if (job) onFinish?.(job)
    }
    wasRunning.current = running
  }, [running, assetId, refreshAsset, refreshOnFinish, job, onFinish])

  const run = async () => {
    setError(null)
    setUnavailable(false)
    try {
      await start(assetId)
      await refresh()
    } catch (err) {
      // Not a failure so much as a signpost: the feature is off, and the place to turn
      // it on is one click away.
      if (apiErrorCode(err) === 'provider_unavailable') setUnavailable(true)
      else setError(apiErrorMessage(err, failureMessage))
    }
  }

  const statusMessages = (
    <>
      {unavailable && (
        <p className={STATUS_CLASS}>
          No AI provider yet.{' '}
          <Link
            to="/settings"
            className="text-blue-600 hover:underline dark:text-blue-400"
          >
            Add one in Settings
          </Link>
          .
        </p>
      )}
      {/* A finished job's own message, which is where a provider failure arrives. */}
      {!running && job?.status === 'error' && job.error_message && (
        <p className={STATUS_ERROR_CLASS}>{job.error_message}</p>
      )}
      {error && <p className={STATUS_ERROR_CLASS}>{error}</p>}
    </>
  )

  if (iconOnly) {
    return (
      <>
        <button
          type="button"
          className="btn btn-ghost p-1.5"
          disabled={running}
          onClick={() => void run()}
          aria-label={running ? job?.stage || runningLabel : label}
          title={running ? job?.stage || runningLabel : label}
        >
          {running ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Icon className="h-3.5 w-3.5" />
          )}
        </button>
        {statusMessages}
      </>
    )
  }

  return (
    <div className="space-y-1">
      <button
        type="button"
        className="btn btn-ghost w-full justify-start text-xs"
        disabled={running}
        onClick={() => void run()}
      >
        <Icon className="mr-1.5 h-3.5 w-3.5" />
        {running ? job?.stage || runningLabel : label}
      </button>

      {statusMessages}
    </div>
  )
}

// Shared with the icon-only layout, whose messages sit outside the button's own
// wrapper div — see AssetDetail's flex-wrap rows, which give them `w-full` so they
// drop onto their own line under the label + button row instead of squeezing beside it.
const STATUS_CLASS = 'w-full text-xs text-gray-500 dark:text-gray-400'
const STATUS_ERROR_CLASS = 'w-full text-xs text-red-600 dark:text-red-400'
