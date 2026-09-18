import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Film, Play, Scissors } from 'lucide-react'
import { clipsApi } from '@/api/clips'
import type { ActivityJob } from '@/api/transcripts'
import { apiErrorMessage } from '@/api/client'
import type { Asset } from '@/api/assets'
import { useLibraryStore } from '@/stores/library'
import { formatDuration } from '@/utils/format'
import EnrichmentButton from '@/components/EnrichmentButton'

/**
 * Cutting a clip or a sub-video from the asset open in the player above.
 *
 * A "dumb panel" like `TranscriptPanel`: it receives `currentTime` and calls `onSeek`
 * rather than holding a ref to the player itself, the pattern `AssetDetail` already
 * uses everywhere else. Only ever mounted for an asset that owns a file — `AssetDetail`
 * hides this tab for a clip, since M7 does not support clipping a clip (`promote`
 * already covers "turn this clip into a real file").
 */

interface Props {
  asset: Asset
  /** Where the player currently is, so "set in/out point here" knows what to capture. */
  currentTime: number
  /** Seconds to jump the player to. The panel does not own the player. */
  onSeek: (seconds: number) => void
}

export default function ClipEditor({ asset, currentTime, onSeek }: Props) {
  const [clips, setClips] = useState<Asset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [inPoint, setInPoint] = useState<number | null>(null)
  const [outPoint, setOutPoint] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null)
  const openById = useLibraryStore((s) => s.openById)

  const load = useCallback(async () => {
    try {
      setClips(await clipsApi.list(asset.id))
      setError(null)
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not load clips'))
    } finally {
      setLoading(false)
    }
  }, [asset.id])

  // Re-seed when a different asset opens in the same panel, the same reason
  // AssetDetail re-seeds its own fields on `asset.id` changing.
  useEffect(() => {
    setLoading(true)
    setInPoint(null)
    setOutPoint(null)
    setCreated(null)
    void load()
  }, [load])

  const rangeValid = inPoint !== null && outPoint !== null && outPoint > inPoint

  const saveClip = async () => {
    if (inPoint === null || outPoint === null || !rangeValid) return
    setSaving(true)
    setError(null)
    setCreated(null)
    try {
      const clip = await clipsApi.create(asset.id, {
        in_point: inPoint,
        out_point: outPoint,
      })
      setClips((current) => [clip, ...current])
      setInPoint(null)
      setOutPoint(null)
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save that clip'))
    } finally {
      setSaving(false)
    }
  }

  // `EnrichmentButton` reports the finished job; `result_asset_id` is only set for
  // this one job kind (M7), and only once — `job.asset_id` stays pointed at `asset`
  // (the source) throughout, so this is the only place to learn what was created.
  const onExtractFinished = (job: ActivityJob) => {
    if (job.status !== 'done' || !job.result_asset_id) return
    const resultId = job.result_asset_id
    void openById(resultId).then((fresh) => {
      setCreated({ id: fresh.id, name: fresh.name })
    })
    setInPoint(null)
    setOutPoint(null)
  }

  if (loading) {
    return <p className="p-4 text-xs text-gray-500 dark:text-gray-400">Loading clips…</p>
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="space-y-2.5 border-b border-gray-200 p-3 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn btn-secondary flex-1 px-2 py-1.5 text-xs"
            onClick={() => setInPoint(currentTime)}
          >
            Set in point
          </button>
          <button
            type="button"
            className="btn btn-secondary flex-1 px-2 py-1.5 text-xs"
            onClick={() => setOutPoint(currentTime)}
          >
            Set out point
          </button>
        </div>

        <div className="flex items-center justify-between text-xs text-gray-600 dark:text-gray-400">
          <span>In: {inPoint === null ? '—' : formatDuration(inPoint)}</span>
          <span>Out: {outPoint === null ? '—' : formatDuration(outPoint)}</span>
          {rangeValid && (
            <button
              type="button"
              className="text-blue-600 hover:underline dark:text-blue-400"
              onClick={() => onSeek(inPoint)}
            >
              Preview
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn btn-primary flex-1 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!rangeValid || saving}
            onClick={() => void saveClip()}
          >
            {saving ? 'Saving…' : 'Save as clip'}
          </button>

          {rangeValid && (
            <div className="flex-1">
              <EnrichmentButton
                assetId={asset.id}
                action="extract_subvideo"
                icon={Scissors}
                label="Extract as sub-video"
                runningLabel="Extracting…"
                start={(id) =>
                  clipsApi.extractSubvideo(id, {
                    in_point: inPoint,
                    out_point: outPoint,
                  })
                }
                failureMessage="Could not start extracting"
                refreshOnFinish={false}
                onFinish={onExtractFinished}
              />
            </div>
          )}
        </div>

        {created && (
          <p className="text-xs text-green-700 dark:text-green-400">
            Created &ldquo;{created.name}&rdquo; —{' '}
            <Link to={`/a/${created.id}`} className="underline">
              view it
            </Link>
          </p>
        )}

        {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {clips.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-gray-500 dark:text-gray-400">
            No clips yet. Set an in and out point above, then save one.
          </p>
        ) : (
          <ol className="divide-y divide-gray-100 dark:divide-gray-800">
            {clips.map((clip) => {
              const isLiveClip = clip.source === 'clip'
              return (
                <li key={clip.id} className="flex items-center gap-2 px-3 py-2 text-xs">
                  {isLiveClip ? (
                    <button
                      type="button"
                      onClick={() => clip.in_point !== null && onSeek(clip.in_point)}
                      className="shrink-0 text-blue-600 hover:underline dark:text-blue-400"
                      title="Play this clip"
                      aria-label="Play this clip"
                    >
                      <Play className="h-3.5 w-3.5" />
                    </button>
                  ) : (
                    <Link
                      to={`/a/${clip.id}`}
                      className="shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                      title="Extracted as a standalone file — open it"
                      aria-label="Open this sub-video"
                    >
                      <Film className="h-3.5 w-3.5" />
                    </Link>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-gray-800 dark:text-gray-200">
                      {clip.name}
                    </p>
                    {isLiveClip && clip.in_point !== null && clip.out_point !== null && (
                      <p className="text-[11px] text-gray-400 dark:text-gray-500">
                        {formatDuration(clip.in_point)}–{formatDuration(clip.out_point)}
                      </p>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        )}
      </div>
    </div>
  )
}
