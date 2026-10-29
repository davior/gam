import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, Loader2, Plus, Trash2 } from 'lucide-react'
import type { Asset } from '@/api/assets'
import { apiErrorMessage } from '@/api/client'
import {
  VERSION_SIZES,
  type AssetVersion,
  versionLabel,
  versionsApi,
} from '@/api/versions'
import { isActive, useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'
import { formatBytes, formatDimensions } from '@/utils/format'

/**
 * A video's standard sizes: the original, and a row per smaller size, made or not.
 *
 * YouTube's model. Each size is made by hand from here, one at a time (the server
 * allows one per video), and each is a file of this asset, not an asset of its own.
 * "Make original" is how space comes back: that size's file replaces the original,
 * which is deleted, while the asset (tags, transcript, clips, links) stays as it is.
 */

interface Props {
  asset: Asset
}

type Confirming = { id: string; action: 'delete' | 'original' } | null

export default function VersionsTab({ asset }: Props) {
  const [versions, setVersions] = useState<AssetVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Which size this panel asked for, to show the progress on its row. A job started
  // elsewhere (or before a reload) still disables every Create, just without a row.
  const [requested, setRequested] = useState<number | null>(null)
  const [confirming, setConfirming] = useState<Confirming>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const job = useActivityStore((s) =>
    s.jobs.find((j) => j.asset_id === asset.id && j.action === 'make_version')
  )
  const refreshActivity = useActivityStore((s) => s.refresh)
  const refreshAsset = useLibraryStore((s) => s.refreshAsset)
  const running = job ? isActive(job) : false
  const currentId = useRef(asset.id)

  const load = useCallback(async () => {
    const assetId = asset.id
    try {
      const rows = await versionsApi.list(assetId)
      // Another asset may have opened in this panel while the request was out.
      if (assetId !== currentId.current) return
      setVersions(rows)
      setError(null)
    } catch (err) {
      if (assetId !== currentId.current) return
      setError(apiErrorMessage(err, 'Could not load versions'))
    } finally {
      if (assetId === currentId.current) setLoading(false)
    }
  }, [asset.id])

  useEffect(() => {
    currentId.current = asset.id
    setLoading(true)
    setVersions([])
    setRequested(null)
    setConfirming(null)
    void load()
  }, [asset.id, load])

  // The finished job wrote a row server-side; read the list again on that edge.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) {
      setRequested(null)
      void load()
    }
    wasRunning.current = running
  }, [running, load])

  const create = async (shortSide: number) => {
    setError(null)
    setRequested(shortSide)
    try {
      await versionsApi.create(asset.id, shortSide)
      await refreshActivity()
    } catch (err) {
      setRequested(null)
      setError(apiErrorMessage(err, 'Could not start making that version'))
    }
  }

  const remove = async (version: AssetVersion) => {
    setBusyId(version.id)
    setError(null)
    try {
      await versionsApi.remove(asset.id, version.id)
      setVersions((rows) => rows.filter((row) => row.id !== version.id))
      setConfirming(null)
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not delete that version'))
    } finally {
      setBusyId(null)
    }
  }

  const makeOriginal = async (version: AssetVersion) => {
    setBusyId(version.id)
    setError(null)
    try {
      await versionsApi.makeOriginal(asset.id, version.id)
      setConfirming(null)
      // The asset's file, size and dimensions changed; the player and Info tab read
      // them from the store.
      await refreshAsset(asset.id)
      await load()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not make that the original'))
    } finally {
      setBusyId(null)
    }
  }

  if (loading) {
    return (
      <p className="p-4 text-xs text-gray-500 dark:text-gray-400">Loading versions…</p>
    )
  }

  const shortSide =
    asset.width && asset.height ? Math.min(asset.width, asset.height) : null
  const bySize = new Map(versions.map((v) => [v.short_side, v]))
  // Every size below the original, plus any version that exists at or above it: one
  // made before an earlier "Make original" swapped in a smaller file.
  const sizes = VERSION_SIZES.filter(
    (size) => shortSide === null || size < shortSide || bySize.has(size)
  )
  const totalBytes = versions.reduce((sum, v) => sum + v.size_bytes, 0)

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <ol className="divide-y divide-gray-100 text-xs dark:divide-gray-800">
        <li className="flex items-center gap-2 px-3 py-2">
          <span className="w-12 shrink-0 font-semibold text-gray-900 dark:text-gray-100">
            Original
          </span>
          <span className="min-w-0 flex-1 truncate text-gray-500 dark:text-gray-400">
            {formatDimensions(asset.width, asset.height)} ·{' '}
            {formatBytes(asset.size_bytes)}
          </span>
        </li>

        {sizes.map((size) => {
          const version = bySize.get(size)
          const isRequested = running && requested === size

          if (!version) {
            return (
              <li key={size} className="flex items-center gap-2 px-3 py-2">
                <span className="w-12 shrink-0 text-gray-700 dark:text-gray-300">
                  {versionLabel(size)}
                </span>
                {isRequested ? (
                  <span className="flex min-w-0 flex-1 items-center gap-1.5 text-gray-500 dark:text-gray-400">
                    <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                    {job?.stage || 'Queued'}
                    {job && job.progress > 1 ? ` · ${job.progress}%` : ''}
                  </span>
                ) : (
                  <>
                    <span className="min-w-0 flex-1 text-gray-400 dark:text-gray-500">
                      Not made
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost px-2 py-1 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={running}
                      onClick={() => void create(size)}
                      title={running ? 'Another version is being made' : undefined}
                    >
                      <Plus className="mr-1 h-3.5 w-3.5" />
                      Create
                    </button>
                  </>
                )}
              </li>
            )
          }

          const asking = confirming?.id === version.id ? confirming.action : null
          const busy = busyId === version.id
          return (
            <li key={size} className="space-y-1.5 px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="w-12 shrink-0 font-medium text-gray-900 dark:text-gray-100">
                  {versionLabel(size)}
                </span>
                <span className="min-w-0 flex-1 truncate text-gray-500 dark:text-gray-400">
                  {formatDimensions(version.width, version.height)} ·{' '}
                  {formatBytes(version.size_bytes)}
                </span>
                {version.file_url && (
                  <a
                    href={version.file_url}
                    download
                    className="shrink-0 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400"
                    title={`Download the ${versionLabel(size)} file`}
                    aria-label={`Download the ${versionLabel(size)} file`}
                  >
                    <Download className="h-3.5 w-3.5" />
                  </a>
                )}
                <button
                  type="button"
                  className="btn btn-ghost shrink-0 px-2 py-1 text-[11px]"
                  disabled={busy || running}
                  onClick={() => setConfirming({ id: version.id, action: 'original' })}
                >
                  Make original
                </button>
                <button
                  type="button"
                  className="shrink-0 text-gray-400 hover:text-red-600 dark:hover:text-red-400"
                  disabled={busy}
                  onClick={() => setConfirming({ id: version.id, action: 'delete' })}
                  title={`Delete the ${versionLabel(size)} version`}
                  aria-label={`Delete the ${versionLabel(size)} version`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>

              {asking && (
                <div className="space-y-1.5 rounded-md border border-amber-200 p-2 dark:border-amber-900">
                  <p className="text-gray-700 dark:text-gray-300">
                    {asking === 'original'
                      ? `Replace the original with the ${versionLabel(size)} file? The original (${formatBytes(asset.size_bytes)}) is deleted and cannot be brought back. Tags, transcript and clips stay.`
                      : `Delete the ${versionLabel(size)} version (${formatBytes(version.size_bytes)})? The original stays.`}
                  </p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      className="btn btn-danger px-2 py-1 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={busy}
                      onClick={() =>
                        void (asking === 'original'
                          ? makeOriginal(version)
                          : remove(version))
                      }
                    >
                      {busy
                        ? 'Working…'
                        : asking === 'original'
                          ? 'Replace original'
                          : 'Delete version'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary px-2 py-1 text-[11px]"
                      disabled={busy}
                      onClick={() => setConfirming(null)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </li>
          )
        })}
      </ol>

      {sizes.length === 0 && (
        <p className="px-3 py-4 text-center text-xs text-gray-500 dark:text-gray-400">
          This video is already at the smallest standard size.
        </p>
      )}

      <div className="space-y-1 border-t border-gray-100 px-3 py-2 text-[11px] text-gray-500 dark:border-gray-800 dark:text-gray-400">
        {versions.length > 0 && (
          <p>
            {versions.length === 1 ? '1 version' : `${versions.length} versions`} ·{' '}
            {formatBytes(totalBytes)}
          </p>
        )}
        {!running && job?.status === 'error' && job.error_message && (
          <p className="text-red-600 dark:text-red-400">{job.error_message}</p>
        )}
        {error && <p className="text-red-600 dark:text-red-400">{error}</p>}
      </div>
    </div>
  )
}
