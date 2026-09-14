import { useCallback, useEffect, useRef, useState } from 'react'
import { Trash2, X } from 'lucide-react'
import type { Asset } from '@/api/assets'
import { tagsApi } from '@/api/tags'
import { apiErrorMessage } from '@/api/client'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'
import { formatBytes, formatDate, formatDimensions, formatDuration } from '@/utils/format'
import AssetThumb from '@/components/AssetThumb'
import EmbedButton from '@/components/EmbedButton'
import TagInput from '@/components/TagInput'
import TranscriptPanel from '@/components/TranscriptPanel'

/** Audio and video can be transcribed; nothing else has speech in it. */
const SPEECH_TYPES = new Set(['audio', 'video'])

interface Props {
  asset: Asset
  onClose: () => void
  /** Seconds to start playback at — a search hit opening at the moment it matched. */
  startAt?: number
}

/** A row of the metadata table, rendered only when there is something to show. */
function Fact({ label, value }: { label: string; value: string }) {
  if (!value) return null
  return (
    <div className="flex justify-between gap-4 py-1 text-xs">
      <dt className="shrink-0 text-gray-500 dark:text-gray-400">{label}</dt>
      <dd className="truncate text-right text-gray-800 dark:text-gray-200">{value}</dd>
    </div>
  )
}

export default function AssetDetail({ asset, onClose, startAt }: Props) {
  const update = useLibraryStore((s) => s.update)
  const remove = useLibraryStore((s) => s.remove)
  const setAssetTags = useLibraryStore((s) => s.setAssetTags)
  const suggestions = useTagStore((s) => s.tags)
  const rememberTags = useTagStore((s) => s.remember)
  const ensureTagsLoaded = useTagStore((s) => s.ensureLoaded)

  const [name, setName] = useState(asset.name)
  const [description, setDescription] = useState(asset.description ?? '')
  const [saving, setSaving] = useState(false)
  const [tagError, setTagError] = useState<string | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const panelRef = useRef<HTMLDivElement>(null)
  // The detail view owns the player element so the transcript can drive it. Passing a
  // ref down beats lifting playback state up: seeking is imperative, and mirroring
  // currentTime into React state on every frame would re-render the whole panel
  // sixty times a second.
  const playerRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null)
  const [currentTime, setCurrentTime] = useState(0)

  // Seek once the player has enough metadata to accept it. Setting currentTime before
  // the browser knows the duration is silently ignored, which is the difference between
  // a search result that opens at the right moment and one that opens at zero.
  const seekOnLoad = useCallback(
    (player: HTMLVideoElement | HTMLAudioElement | null) => {
      playerRef.current = player
      if (!player || startAt === undefined) return

      const apply = () => {
        player.currentTime = startAt
      }
      if (player.readyState >= 1) {
        apply()
      } else {
        player.addEventListener('loadedmetadata', apply, { once: true })
      }
    },
    [startAt]
  )

  const seekTo = useCallback((seconds: number) => {
    const player = playerRef.current
    if (!player) return
    player.currentTime = seconds
    void player.play()?.catch(() => {
      // Autoplay can be refused; the seek still happened, which is what was asked for.
    })
  }, [])

  // Re-seed when a different asset opens in the same panel, or the fields would keep
  // showing the previous one's values.
  useEffect(() => {
    setName(asset.name)
    setDescription(asset.description ?? '')
    setConfirmingDelete(false)
    setTagError(null)
    setCurrentTime(startAt ?? 0)
  }, [asset.id, asset.name, asset.description, startAt])

  // The panel can be reached from search, which never renders the filter bar, so it
  // asks for the catalogue itself rather than assuming somebody else did.
  useEffect(() => {
    ensureTagsLoaded()
  }, [ensureTagsLoaded])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const dirty = name.trim() !== asset.name || description !== (asset.description ?? '')

  const save = async () => {
    if (!dirty || !name.trim()) return
    setSaving(true)
    try {
      await update(asset.id, { name: name.trim(), description: description || null })
    } catch {
      // The store restores the server's version and surfaces the message; the panel
      // stays open so the edit is not lost.
    } finally {
      setSaving(false)
    }
  }

  // Both tag endpoints answer with the asset's *whole* tag set rather than a delta, so
  // the store is handed the server's list instead of one computed here — there is no
  // second version of the truth to drift.
  const addTags = async (names: string[]) => {
    setTagError(null)
    try {
      const tags = await tagsApi.addToAsset(asset.id, names)
      setAssetTags(asset.id, tags)
      rememberTags(tags)
    } catch (error) {
      setTagError(apiErrorMessage(error, 'Could not add that tag'))
    }
  }

  const removeTag = async (tagId: string) => {
    setTagError(null)
    try {
      setAssetTags(asset.id, await tagsApi.removeFromAsset(asset.id, tagId))
    } catch (error) {
      setTagError(apiErrorMessage(error, 'Could not remove that tag'))
    }
  }

  const confirmDelete = async () => {
    try {
      await remove(asset.id)
      onClose()
    } catch {
      setConfirmingDelete(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={asset.name}
        className="card flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden p-0"
      >
        <header className="flex items-center justify-between gap-3 border-b border-gray-200 px-4 py-2.5 dark:border-gray-700">
          <h2 className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
            {asset.name}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost p-1.5"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col overflow-auto md:flex-row">
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex min-h-[240px] flex-1 items-center justify-center bg-gray-900 p-2">
              <Preview
                asset={asset}
                attachPlayer={seekOnLoad}
                onTimeUpdate={setCurrentTime}
              />
            </div>

            {SPEECH_TYPES.has(asset.asset_type) && (
              <div className="flex max-h-[38vh] min-h-0 flex-col border-t border-gray-200 dark:border-gray-700">
                <TranscriptPanel
                  assetId={asset.id}
                  onSeek={seekTo}
                  currentTime={currentTime}
                />
              </div>
            )}
          </div>

          <div className="w-full shrink-0 space-y-4 p-4 md:w-80">
            <div>
              <label className="label" htmlFor="asset-name">
                Name
              </label>
              <input
                id="asset-name"
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <div>
              <label className="label" htmlFor="asset-description">
                Description
              </label>
              <textarea
                id="asset-description"
                className="input min-h-[80px] resize-y"
                placeholder="What is in this? Anything you write here is searchable."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>

            <button
              type="button"
              onClick={save}
              disabled={!dirty || saving || !name.trim()}
              className="btn btn-primary w-full disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
            </button>

            <div>
              <TagInput
                tags={asset.tags}
                suggestions={suggestions}
                onAdd={(names) => void addTags(names)}
                onRemove={(tagId) => void removeTag(tagId)}
                label="Tags"
              />
              {tagError && (
                <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">
                  {tagError}
                </p>
              )}
            </div>

            <EmbedButton assetId={asset.id} />

            <dl className="divide-y divide-gray-100 border-t border-gray-100 pt-2 dark:divide-gray-800 dark:border-gray-800">
              <Fact label="Type" value={asset.asset_type} />
              <Fact label="Format" value={asset.file_format ?? ''} />
              <Fact label="Size" value={formatBytes(asset.size_bytes)} />
              <Fact label="Duration" value={formatDuration(asset.duration_seconds)} />
              <Fact
                label="Dimensions"
                value={formatDimensions(asset.width, asset.height)}
              />
              <Fact label="Codec" value={asset.codec ?? ''} />
              <Fact label="Original name" value={asset.original_name ?? ''} />
              <Fact label="Added" value={formatDate(asset.upload_date)} />
            </dl>

            {confirmingDelete ? (
              <div className="space-y-2 rounded-md border border-red-200 p-3 dark:border-red-900">
                <p className="text-xs text-gray-700 dark:text-gray-300">
                  Delete this asset and its file? This cannot be undone.
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={confirmDelete}
                    className="btn btn-danger flex-1"
                  >
                    Delete
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmingDelete(false)}
                    className="btn btn-secondary flex-1"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmingDelete(true)}
                className="btn btn-ghost w-full text-red-600 dark:text-red-400"
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                Delete
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** The asset itself, played or shown in place. */
function Preview({
  asset,
  attachPlayer,
  onTimeUpdate,
}: {
  asset: Asset
  attachPlayer: (player: HTMLVideoElement | HTMLAudioElement | null) => void
  onTimeUpdate: (seconds: number) => void
}) {
  if (asset.missing || !asset.file_url) {
    return (
      <div className="p-8 text-center text-sm text-gray-400">
        {asset.missing ? 'This file is missing from storage.' : 'No preview available.'}
      </div>
    )
  }

  if (asset.asset_type === 'video') {
    // controls + preload="metadata": the browser fetches enough to show a duration and
    // enable seeking without downloading the whole file. The Range support on the
    // server is what makes that work.
    return (
      <video
        key={asset.id}
        ref={attachPlayer}
        src={asset.file_url}
        controls
        preload="metadata"
        poster={asset.thumb_url ?? undefined}
        onTimeUpdate={(e) => onTimeUpdate(e.currentTarget.currentTime)}
        className="max-h-[45vh] w-full"
      />
    )
  }

  if (asset.asset_type === 'audio') {
    return (
      <audio
        key={asset.id}
        ref={attachPlayer}
        src={asset.file_url}
        controls
        preload="metadata"
        onTimeUpdate={(e) => onTimeUpdate(e.currentTarget.currentTime)}
        className="w-full px-4"
      />
    )
  }

  if (asset.asset_type === 'image') {
    return (
      <img
        src={asset.file_url}
        alt={asset.name}
        className="max-h-[60vh] object-contain"
      />
    )
  }

  return (
    <div className="flex flex-col items-center gap-3 p-8">
      <AssetThumb asset={asset} className="h-40 w-32 rounded" />
      <a
        href={asset.file_url}
        target="_blank"
        rel="noreferrer"
        className="btn btn-secondary text-xs"
      >
        Open document
      </a>
    </div>
  )
}
