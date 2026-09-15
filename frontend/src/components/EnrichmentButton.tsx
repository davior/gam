import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
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
    if (wasRunning.current && !running && refreshOnFinish) void refreshAsset(assetId)
    wasRunning.current = running
  }, [running, assetId, refreshAsset, refreshOnFinish])

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

      {unavailable && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
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
      {/* A finished job's own message, which is where "you wrote this yourself" and any
          provider failure arrive. */}
      {!running && job?.status === 'error' && job.error_message && (
        <p className="text-xs text-red-600 dark:text-red-400">{job.error_message}</p>
      )}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  )
}
