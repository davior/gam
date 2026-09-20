import { useCallback, useEffect, useState } from 'react'
import {
  Eye,
  FileText,
  Info,
  Link2,
  Mic,
  Pencil,
  Quote,
  ScanText,
  Scissors,
  Tags as TagsIcon,
  Trash2,
  Wand2,
  X,
} from 'lucide-react'
import type { Asset, AssetUpdate } from '@/api/assets'
import { tagsApi } from '@/api/tags'
import { enrichmentApi } from '@/api/enrichment'
import { clipsApi } from '@/api/clips'
import { activityApi } from '@/api/transcripts'
import { formatCost, usageApi, type UsageTotals } from '@/api/usage'
import { apiErrorMessage } from '@/api/client'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'
import { formatBytes, formatDate, formatDimensions, formatDuration } from '@/utils/format'
import { useAutoGrow } from '@/utils/useAutoGrow'
import AssetThumb from '@/components/AssetThumb'
import AttributionPanel from '@/components/AttributionPanel'
import ClipEditor from '@/components/ClipEditor'
import DocumentTextPanel from '@/components/DocumentTextPanel'
import EmbedButton from '@/components/EmbedButton'
import EnrichmentButton from '@/components/EnrichmentButton'
import SuggestionPanel from '@/components/SuggestionPanel'
import TagInput from '@/components/TagInput'
import Tabs, { type TabSpec } from '@/components/Tabs'
import TranscriptPanel from '@/components/TranscriptPanel'

/** A clip has no bytes, no transcript and no AI enrichment of its own — a promoted or
 *  freshly-extracted sub-video does, and `source` (not `parent_asset_id`, which both
 *  carry as provenance) is what tells the two apart. */
function ownsNoFile(asset: Asset): boolean {
  return asset.source === 'clip'
}

/** Poll a set of M7 extraction/promotion jobs to a terminal state, by id rather than
 *  through the activity store's capped `jobs` list — the promote-all flow can be
 *  waiting on more jobs than that list is guaranteed to still contain. Throws with the
 *  first failure's message if any job ends in error. */
async function waitForJobs(jobIds: string[]): Promise<void> {
  let remaining = jobIds
  while (remaining.length > 0) {
    const jobs = await Promise.all(
      remaining.map((id) => activityApi.get('enrichment', id))
    )
    const failed = jobs.find((j) => j.status === 'error')
    if (failed) throw new Error(failed.error_message ?? 'A clip could not be promoted')
    remaining = jobs
      .filter((j) => j.status === 'queued' || j.status === 'processing')
      .map((j) => j.id)
    // Checked before waiting, not after: a promote that already finished by the time
    // this polls should not pay a fixed delay it does not need.
    if (remaining.length > 0) {
      await new Promise((resolve) => setTimeout(resolve, 1500))
    }
  }
}

/** Audio and video can be transcribed; nothing else has speech in it. */
const SPEECH_TYPES = new Set(['audio', 'video'])

/** Documents have text read out of them instead — the same idea, a different source. */
const TEXT_TYPE = 'document'

/**
 * Types with something to look at. They get a media well worth giving height to, and
 * they are the only ones that gain anything from the side-by-side layout — a waveform-less
 * audio bar beside a transcript would just be a tall black rectangle.
 */
const VISUAL_TYPES = new Set(['video', 'image'])

interface Props {
  asset: Asset
  onClose: () => void
  /** Seconds to start playback at — a search hit opening at the moment it matched. */
  startAt?: number
  /** What the close control says. `/a/:id` goes back to the library rather than closing. */
  closeLabel?: string
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

/**
 * Everything about one asset, and nothing about where it sits.
 *
 * Positioning belongs to `DetailDock` — this fills whatever box it is handed, whether
 * that is a panel docked beside the library, a full-screen sheet on a phone, or the
 * whole of `/a/:id`. The root is a container query context, so the layout answers to the
 * width it actually has rather than the viewport's: drag the panel past `@4xl` and the
 * media moves beside the tabs instead of sitting above them.
 */
export default function AssetDetail({
  asset,
  onClose,
  startAt,
  closeLabel = 'Close',
}: Props) {
  const update = useLibraryStore((s) => s.update)
  const remove = useLibraryStore((s) => s.remove)
  const refreshAsset = useLibraryStore((s) => s.refreshAsset)
  const setAssetTags = useLibraryStore((s) => s.setAssetTags)
  const suggestions = useTagStore((s) => s.tags)
  const rememberTags = useTagStore((s) => s.remember)
  const ensureTagsLoaded = useTagStore((s) => s.ensureLoaded)

  const [name, setName] = useState(asset.name)
  const [description, setDescription] = useState(asset.description ?? '')
  const [summary, setSummary] = useState(asset.summary ?? '')
  const [saving, setSaving] = useState(false)
  const [tagError, setTagError] = useState<string | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  // Set once `remove()` reports `asset_has_dependent_clips` — the confirm block
  // switches from "delete this?" to "promote these, then delete" while this is set.
  const [dependentClips, setDependentClips] = useState<Asset[] | null>(null)
  const [promoting, setPromoting] = useState(false)
  const [promoteError, setPromoteError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const descriptionRef = useAutoGrow(description)
  const summaryRef = useAutoGrow(summary)

  // The detail view owns the player element so the transcript can drive it. Passing a
  // ref down beats lifting playback state up: seeking is imperative, and mirroring
  // currentTime into React state on every frame would re-render the whole panel
  // sixty times a second.
  const [player, setPlayer] = useState<HTMLVideoElement | HTMLAudioElement | null>(null)
  const [currentTime, setCurrentTime] = useState(0)

  // Seek once the player has enough metadata to accept it. Setting currentTime before
  // the browser knows the duration is silently ignored, which is the difference between
  // a search result that opens at the right moment and one that opens at zero.
  const attachPlayer = useCallback(
    (element: HTMLVideoElement | HTMLAudioElement | null) => {
      setPlayer(element)
      // A clip's own bound, when nothing more specific (a search hit's timestamp) was
      // asked for — a clip opens at its in point, not at the parent's start.
      const effectiveStart = startAt ?? asset.in_point ?? undefined
      if (!element || effectiveStart === undefined) return

      const apply = () => {
        element.currentTime = effectiveStart
      }
      if (element.readyState >= 1) {
        apply()
      } else {
        element.addEventListener('loadedmetadata', apply, { once: true })
      }
    },
    [startAt, asset.in_point]
  )

  const seekTo = useCallback(
    (seconds: number) => {
      if (!player) return
      player.currentTime = seconds
      void player.play()?.catch(() => {
        // Autoplay can be refused; the seek still happened, which is what was asked for.
      })
    },
    [player]
  )

  // Re-seed when a different asset opens in the same panel, or the fields would keep
  // showing the previous one's values.
  useEffect(() => {
    setName(asset.name)
    setDescription(asset.description ?? '')
    // Also what makes a finished summarise job appear without a reload: the store
    // replaces the asset, the effect re-seeds, and the textarea shows the new text.
    setSummary(asset.summary ?? '')
    setConfirmingDelete(false)
    setDependentClips(null)
    setPromoteError(null)
    setTagError(null)
    setCurrentTime(startAt ?? asset.in_point ?? 0)
  }, [asset.id, asset.name, asset.description, asset.summary, asset.in_point, startAt])

  // The panel can be reached from search, which never renders the filter bar, so it
  // asks for the catalogue itself rather than assuming somebody else did.
  useEffect(() => {
    ensureTagsLoaded()
  }, [ensureTagsLoaded])

  // The /a/{id} URL, which is what GN-4 says a Notes document should link to. Built from
  // `window.location.origin` rather than a configured base: whichever host the user is
  // looking at is the one their colleague can reach too.
  const copyLink = useCallback(async () => {
    const url = `${window.location.origin}/a/${asset.id}`
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access needs a secure context and a user gesture, and is refused
      // outright in some browsers. Prompting with the URL beats a button that silently
      // does nothing.
      window.prompt('Copy this link', url)
    }
  }, [asset.id])

  // What enrichment has cost on this asset. Re-read whenever the asset changes, which
  // includes after a job finishes — EnrichmentButton refreshes it, and that bumps
  // `metadata_modified_date`, so this picks up the new spend without its own poll.
  const [usage, setUsage] = useState<UsageTotals | null>(null)
  useEffect(() => {
    let current = true
    usageApi
      .forAsset(asset.id)
      .then((totals) => {
        if (current) setUsage(totals)
      })
      .catch(() => {
        // A cost line is not worth an error banner over.
        if (current) setUsage(null)
      })
    return () => {
      current = false
    }
  }, [asset.id, asset.metadata_modified_date])

  const dirty =
    name.trim() !== asset.name ||
    description !== (asset.description ?? '') ||
    summary !== (asset.summary ?? '')

  const save = async () => {
    if (!dirty || !name.trim()) return
    setSaving(true)
    try {
      // Only the fields that actually changed: the API marks every key it receives as
      // human-written provenance (FR 8.1.3 bookkeeping), so sending name/description/
      // summary as a fixed trio would stamp the two you didn't touch right alongside
      // the one you did — including while they're still empty.
      const changes: AssetUpdate = {}
      if (name.trim() !== asset.name) changes.name = name.trim()
      if (description !== (asset.description ?? ''))
        changes.description = description || null
      if (summary !== (asset.summary ?? '')) changes.summary = summary || null

      await update(asset.id, changes)
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
    // Checked before calling `remove()`, not caught from its rejection: `remove`
    // optimistically drops the asset from the store *before* the request resolves,
    // which makes `AssetView`'s `assets.find(...)` briefly come back `undefined` —
    // unmounting this whole component — and remounting it fresh once the store
    // restores the asset on failure. That is invisible for a plain failure (it just
    // resets `confirmingDelete` to what a fresh mount already starts at), but it would
    // silently discard `setDependentClips` below, called on a component instance that
    // no longer exists by the time the guard's 409 comes back. Checking first means
    // the guarded path never calls `remove()` at all, so it never hits that cycle.
    try {
      const children = await clipsApi.list(asset.id)
      const blocking = children.filter((c) => c.source === 'clip')
      if (blocking.length > 0) {
        setDependentClips(blocking)
        return
      }
    } catch {
      // Could not even check — fall through and let the real delete attempt, and its
      // own error handling below, be the source of truth.
    }

    try {
      await remove(asset.id)
      onClose()
    } catch {
      setConfirmingDelete(false)
    }
  }

  const promoteAllAndDelete = async () => {
    if (!dependentClips || dependentClips.length === 0) return
    setPromoting(true)
    setPromoteError(null)
    try {
      const jobs = await Promise.all(dependentClips.map((c) => clipsApi.promote(c.id)))
      await waitForJobs(jobs.map((j) => j.id))
      await Promise.all(dependentClips.map((c) => refreshAsset(c.id)))
      await remove(asset.id)
      onClose()
    } catch (err) {
      setPromoteError(apiErrorMessage(err, 'Could not promote all of them'))
    } finally {
      setPromoting(false)
    }
  }

  const visual = VISUAL_TYPES.has(asset.asset_type)

  const details = (
    <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
      {/* One button that runs describe, summarise and autotag in turn, for anyone who
          would otherwise press the three icon buttons below one after another. Absent
          for a clip (M7): it has no bytes and no transcript of its own to read from —
          see `enrichment/source.py::gather()`'s guard — so offering the button would
          just be an immediate, confusing failure. */}
      {!ownsNoFile(asset) && (
        <EnrichmentButton
          assetId={asset.id}
          action="generate_all"
          icon={Wand2}
          label="Generate all"
          runningLabel="Generating…"
          start={enrichmentApi.generateAll}
          failureMessage="Could not start generating"
        />
      )}

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

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <label className="label mb-0" htmlFor="asset-description">
            Description
          </label>
          {/* Beside the label it writes into, rather than as its own row below the
              box — describe fills this field, and that is what the icon says. */}
          {!ownsNoFile(asset) && (
            <EnrichmentButton
              assetId={asset.id}
              action="describe"
              icon={Eye}
              label="Describe with AI"
              runningLabel="Describing…"
              start={enrichmentApi.describe}
              failureMessage="Could not start describing"
              iconOnly
            />
          )}
        </div>
        <textarea
          id="asset-description"
          ref={descriptionRef}
          className="input min-h-[6rem] resize-none overflow-hidden"
          placeholder="What is in this? Anything you write here is searchable."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <label className="label mb-0" htmlFor="asset-summary">
            Summary
          </label>
          {!ownsNoFile(asset) && (
            <EnrichmentButton
              assetId={asset.id}
              action="summarize"
              icon={FileText}
              label="Summarise with AI"
              runningLabel="Summarising…"
              start={enrichmentApi.summarize}
              failureMessage="Could not start summarising"
              iconOnly
            />
          )}
        </div>
        <textarea
          id="asset-summary"
          ref={summaryRef}
          className="input min-h-[6rem] resize-none overflow-hidden"
          placeholder="Written by AI, or by you. Searchable either way."
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
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
          labelAdornment={
            !ownsNoFile(asset) && (
              <EnrichmentButton
                assetId={asset.id}
                action="autotag"
                icon={TagsIcon}
                label="Suggest tags and a title"
                runningLabel="Tagging…"
                start={enrichmentApi.autotag}
                failureMessage="Could not start tagging"
                refreshOnFinish={false}
                iconOnly
              />
            )
          }
        />
        {tagError && (
          <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">{tagError}</p>
        )}
      </div>

      <SuggestionPanel assetId={asset.id} />
    </div>
  )

  const info = (
    <div className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
      {asset.asset_type === TEXT_TYPE && (
        <EnrichmentButton
          assetId={asset.id}
          action="extract_text"
          icon={ScanText}
          label="Extract text"
          runningLabel="Reading…"
          start={enrichmentApi.extractText}
          failureMessage="Could not start reading this document"
          refreshOnFinish={false}
        />
      )}
      <EmbedButton assetId={asset.id} />

      <dl className="divide-y divide-gray-100 border-t border-gray-100 pt-2 dark:divide-gray-800 dark:border-gray-800">
        <Fact label="Type" value={asset.asset_type} />
        {asset.in_point !== null && asset.out_point !== null && (
          <Fact
            label="Clip"
            value={`${formatDuration(asset.in_point)}–${formatDuration(asset.out_point)}`}
          />
        )}
        <Fact label="Format" value={asset.file_format ?? ''} />
        <Fact label="Size" value={formatBytes(asset.size_bytes)} />
        <Fact label="Duration" value={formatDuration(asset.duration_seconds)} />
        <Fact label="Dimensions" value={formatDimensions(asset.width, asset.height)} />
        <Fact label="Codec" value={asset.codec ?? ''} />
        <Fact label="Original name" value={asset.original_name ?? ''} />
        {/* The resolved credit, so it is readable without opening the Source tab — and
            visible on a clip, which shows what it inherited. */}
        <Fact label="Credit" value={asset.credit} />
        <Fact label="Added" value={formatDate(asset.upload_date)} />
        {usage && usage.total_events > 0 && (
          <Fact
            label="AI cost (est.)"
            value={
              usage.priced_events > 0
                ? formatCost(usage.cost, usage.currency)
                : 'not priced'
            }
          />
        )}
      </dl>

      {dependentClips ? (
        <div className="space-y-2 rounded-md border border-amber-200 p-3 dark:border-amber-900">
          <p className="text-xs text-gray-700 dark:text-gray-300">
            {dependentClips.length} clip{dependentClips.length === 1 ? '' : 's'} depend on
            this asset. Extract {dependentClips.length === 1 ? 'it' : 'them'} as sub-video
            {dependentClips.length === 1 ? '' : 's'} first, then this can be deleted.
          </p>
          <ul className="max-h-24 space-y-0.5 overflow-auto text-[11px] text-gray-500 dark:text-gray-400">
            {dependentClips.map((clip) => (
              <li key={clip.id} className="truncate">
                {clip.name}
              </li>
            ))}
          </ul>
          {promoteError && (
            <p className="text-xs text-red-600 dark:text-red-400">{promoteError}</p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void promoteAllAndDelete()}
              disabled={promoting}
              className="btn btn-danger flex-1 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {promoting ? 'Promoting…' : 'Promote and delete'}
            </button>
            <button
              type="button"
              onClick={() => {
                setDependentClips(null)
                setConfirmingDelete(false)
                setPromoteError(null)
              }}
              disabled={promoting}
              className="btn btn-secondary flex-1 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : confirmingDelete ? (
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
  )

  // Neither applies to a clip: it has no transcript of its own (only the parent does,
  // for the whole recording rather than this range), and clipping a clip is out of
  // scope for M7 — `promote` already covers "turn this clip into a real file".
  const clippable = SPEECH_TYPES.has(asset.asset_type) && !ownsNoFile(asset)

  const tabs: TabSpec[] = [
    { id: 'details', label: 'Details', icon: Pencil, content: details },
    ...(clippable
      ? [
          {
            id: 'transcript',
            label: 'Transcript',
            icon: Mic,
            content: (
              <TranscriptPanel
                assetId={asset.id}
                onSeek={seekTo}
                currentTime={currentTime}
              />
            ),
          },
          {
            id: 'clip',
            label: 'Clip',
            icon: Scissors,
            content: (
              <ClipEditor asset={asset} currentTime={currentTime} onSeek={seekTo} />
            ),
          },
        ]
      : []),
    ...(asset.asset_type === TEXT_TYPE
      ? [
          {
            id: 'text',
            label: 'Text',
            icon: FileText,
            content: <DocumentTextPanel assetId={asset.id} />,
          },
        ]
      : []),
    {
      id: 'attribution',
      label: 'Source',
      icon: Quote,
      content: <AttributionPanel asset={asset} />,
    },
    { id: 'info', label: 'Info', icon: Info, content: info },
  ]

  return (
    <div className="@container flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-gray-200 px-4 py-2.5 dark:border-gray-700">
        <h2 className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
          {asset.name}
        </h2>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => void copyLink()}
            className="btn btn-ghost p-1.5"
            aria-label="Copy link to this asset"
            title={copied ? 'Link copied' : 'Copy link to this asset'}
          >
            <Link2
              className={`h-4 w-4 ${copied ? 'text-green-600 dark:text-green-400' : ''}`}
            />
          </button>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost p-1.5"
            aria-label={closeLabel}
            title={closeLabel}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>

      <div className={`flex min-h-0 flex-1 flex-col ${visual ? '@4xl:flex-row' : ''}`}>
        <div
          className={`flex items-center justify-center bg-gray-900 ${
            visual
              ? 'aspect-video w-full shrink-0 @4xl:aspect-auto @4xl:h-full @4xl:min-h-0 @4xl:w-auto @4xl:flex-1'
              : 'w-full shrink-0 py-4'
          }`}
        >
          <Preview
            asset={asset}
            attachPlayer={attachPlayer}
            onTimeUpdate={setCurrentTime}
          />
        </div>

        <div
          className={`flex min-h-0 flex-1 flex-col border-t border-gray-200 dark:border-gray-700 ${
            visual
              ? '@4xl:w-[26rem] @4xl:flex-none @4xl:border-l @4xl:border-t-0 @6xl:w-[32rem]'
              : ''
          }`}
        >
          <Tabs tabs={tabs} />
        </div>
      </div>
    </div>
  )
}

/** The asset itself, played or shown in place. Fills the well it is given. */
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

  // A clip's bound is enforced here, client-side — nothing server-side (`media/
  // ranged.py`) knows about trim points, the same as the parent's bytes are served
  // unchanged either way. `asset.out_point` is null for anything that is not a live
  // clip, so this is a no-op for an ordinary asset.
  const handleTimeUpdate = (
    e: React.SyntheticEvent<HTMLVideoElement | HTMLAudioElement>
  ) => {
    const time = e.currentTarget.currentTime
    onTimeUpdate(time)
    if (asset.out_point !== null && time >= asset.out_point) {
      e.currentTarget.pause()
    }
  }

  if (asset.asset_type === 'video') {
    // controls + preload="metadata": the browser fetches enough to show a duration and
    // enable seeking without downloading the whole file. The Range support on the
    // server is what makes that work.
    //
    // h-full + object-contain rather than a viewport cap: the well decides how much room
    // there is, and the video letterboxes into it whatever shape the panel is dragged to.
    return (
      <video
        key={asset.id}
        ref={attachPlayer}
        src={asset.file_url}
        controls
        preload="metadata"
        poster={asset.thumb_url ?? undefined}
        onTimeUpdate={handleTimeUpdate}
        className="h-full w-full object-contain"
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
        onTimeUpdate={handleTimeUpdate}
        className="w-full max-w-2xl px-4"
      />
    )
  }

  if (asset.asset_type === 'image') {
    return (
      <img
        src={asset.file_url}
        alt={asset.name}
        className="h-full w-full object-contain"
      />
    )
  }

  return (
    <div className="flex flex-col items-center gap-3 px-8 py-4">
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
