import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { FileText } from 'lucide-react'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { enrichmentApi } from '@/api/enrichment'
import { isActive, useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'

/**
 * Summarise one asset with the configured AI provider.
 *
 * Same shape as EmbedButton, deliberately: it owns no timer, reading its own asset's
 * job out of the activity store the app is already polling rather than opening a second
 * loop per open modal. The two "not configured" cases are signposts rather than errors —
 * the feature is off and the place to turn it on is one click away.
 */

interface Props {
  assetId: string
}

export default function SummarizeButton({ assetId }: Props) {
  const job = useActivityStore((s) =>
    s.jobs.find((j) => j.asset_id === assetId && j.action === 'summarize')
  )
  const refresh = useActivityStore((s) => s.refresh)
  const refreshAsset = useLibraryStore((s) => s.refreshAsset)
  const [error, setError] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  const running = job ? isActive(job) : false

  // The job writes the summary server-side, so the asset in the store is stale the
  // moment it finishes. Re-read it once on the running -> finished edge; without this
  // the panel keeps showing an empty summary until the library is reloaded.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) void refreshAsset(assetId)
    wasRunning.current = running
  }, [running, assetId, refreshAsset])

  const start = async () => {
    setError(null)
    setUnavailable(false)
    try {
      await enrichmentApi.summarize(assetId)
      await refresh()
    } catch (err) {
      if (apiErrorCode(err) === 'provider_unavailable') setUnavailable(true)
      else setError(apiErrorMessage(err, 'Could not start summarising'))
    }
  }

  return (
    <div className="space-y-1">
      <button
        type="button"
        className="btn btn-ghost w-full justify-start text-xs"
        disabled={running}
        onClick={() => void start()}
      >
        <FileText className="mr-1.5 h-3.5 w-3.5" />
        {running ? job?.stage || 'Summarising…' : 'Summarise with AI'}
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
      {/* A finished job's own message, which is where "you wrote this summary yourself"
          and any provider failure arrive. */}
      {!running && job?.status === 'error' && job.error_message && (
        <p className="text-xs text-red-600 dark:text-red-400">{job.error_message}</p>
      )}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  )
}
