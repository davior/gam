import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { FileQuestion } from 'lucide-react'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { useLibraryStore } from '@/stores/library'
import AssetDetail from '@/components/AssetDetail'

/**
 * One asset, at its own URL.
 *
 * `docs/gecko-notes-integration.md` GN-4 settles the Notes→GAM asset reference as a
 * plain link to `/a/{assetId}`, and chose that over a custom BlockNote block partly
 * *because* it needed no work in Notes. That reasoning assumed this end existed; it did
 * not, and until now `/a/anything` fell through the catch-all route to the library with
 * no sign anything had gone wrong.
 *
 * Renders the same `AssetDetail` the library modal does, reading from the library store
 * so its edit, delete and tag controls write to the same place they always have.
 */
export default function AssetView() {
  const { id = '' } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const openById = useLibraryStore((s) => s.openById)
  const asset = useLibraryStore((s) => s.assets.find((a) => a.id === id))
  const [loading, setLoading] = useState(!asset)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    setMissing(false)
    setError(null)
    setLoading(true)
    openById(id)
      .then(() => {
        if (current) setLoading(false)
      })
      .catch((err: unknown) => {
        if (!current) return
        setLoading(false)
        if (apiErrorCode(err) === 'not_found') setMissing(true)
        else setError(apiErrorMessage(err, 'Could not load this asset'))
      })
    return () => {
      current = false
    }
  }, [id, openById])

  // A search hit deep-links to the moment it matched: /a/{id}?t=412.0. `Number('')` is
  // 0, not NaN, so the presence check has to come first — otherwise every link without
  // a timestamp would seek to the start, which looks like a bug on a video the user had
  // already scrubbed.
  const raw = params.get('t')
  const parsed = raw === null ? Number.NaN : Number(raw)
  const startAt = Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined

  if (loading && !asset) {
    return (
      <p className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
        Loading…
      </p>
    )
  }

  if (missing || (!asset && !error)) {
    return <NotFound onBack={() => navigate('/library')} />
  }

  if (error && !asset) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-6">
        <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </p>
      </div>
    )
  }

  if (!asset) return null

  return (
    <AssetDetail asset={asset} startAt={startAt} onClose={() => navigate('/library')} />
  )
}

function NotFound({ onBack }: { onBack: () => void }) {
  return (
    <div className="py-16 text-center">
      <FileQuestion className="mx-auto h-8 w-8 text-gray-400 dark:text-gray-500" />
      <h2 className="mt-3 text-sm font-semibold text-gray-900 dark:text-gray-100">
        That asset is not here
      </h2>
      <p className="mx-auto mt-1 max-w-sm text-sm text-gray-500 dark:text-gray-400">
        It may have been deleted, or the link may belong to someone else's library.
      </p>
      <button type="button" className="btn btn-secondary mt-4" onClick={onBack}>
        Back to the library
      </button>
    </div>
  )
}
