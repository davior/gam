import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { embeddingsApi } from '@/api/embeddings'
import { isActive, useActivityStore } from '@/stores/activity'

/**
 * Embed one asset for semantic search.
 *
 * The transcript panel covers audio and video, which embed themselves when transcribed.
 * This is for everything else — an image or a PDF, whose name and description are the
 * only thing that makes it findable by meaning — and for re-embedding after an edit.
 *
 * Owns no timer: the activity store is already polling, so this reads its own asset's
 * job out of that rather than opening a second loop per open modal.
 */

interface Props {
  assetId: string
}

export default function EmbedButton({ assetId }: Props) {
  const job = useActivityStore((s) =>
    s.jobs.find((j) => j.asset_id === assetId && j.action === 'embed')
  )
  const refresh = useActivityStore((s) => s.refresh)
  const [error, setError] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  const running = job ? isActive(job) : false

  const start = async () => {
    setError(null)
    setUnavailable(false)
    try {
      await embeddingsApi.embedAsset(assetId)
      await refresh()
    } catch (err) {
      // Not a failure so much as a signpost: the feature is off, and the place to turn
      // it on is one click away.
      if (apiErrorCode(err) === 'embedding_unavailable') setUnavailable(true)
      else setError(apiErrorMessage(err, 'Could not start embedding'))
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
        <Sparkles className="mr-1.5 h-3.5 w-3.5" />
        {running ? job?.stage || 'Embedding…' : 'Embed for semantic search'}
      </button>

      {unavailable && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          No embedding provider yet.{' '}
          <Link
            to="/settings"
            className="text-blue-600 hover:underline dark:text-blue-400"
          >
            Add one in Settings
          </Link>
          .
        </p>
      )}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  )
}
