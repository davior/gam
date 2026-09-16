import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Eye, FileText, ScanText, Sparkles, Tags, X } from 'lucide-react'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { enrichmentApi, type BulkAction } from '@/api/enrichment'
import { isActive, useActivityStore } from '@/stores/activity'
import type { Asset } from '@/api/assets'
import type { Tag } from '@/api/tags'
import TagInput from '@/components/TagInput'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'

/**
 * Bulk actions for the current selection: tags, and enrichment.
 *
 * The remove side lists only tags that are actually on something selected — offering
 * the whole catalogue would make "remove" mostly a list of no-ops, and hide the two
 * entries that would do anything.
 *
 * The enrichment row is one job for the whole selection rather than one per asset, so
 * there is one progress bar and one Cancel. It is also where M5's deferred
 * "embed this selection" finally lands.
 */

const ENRICHMENTS: Array<{
  action: BulkAction
  label: string
  icon: typeof Eye
}> = [
  { action: 'extract_text', label: 'Extract text', icon: ScanText },
  { action: 'describe', label: 'Describe', icon: Eye },
  { action: 'summarize', label: 'Summarise', icon: FileText },
  { action: 'autotag', label: 'Suggest tags', icon: Tags },
  { action: 'embed', label: 'Embed', icon: Sparkles },
]

interface Props {
  selected: Set<string>
  assets: Asset[]
  onSelectAll: () => void
  onClear: () => void
}

export default function SelectionBar({ selected, assets, onSelectAll, onClear }: Props) {
  const applyTags = useLibraryStore((s) => s.applyTags)
  const suggestions = useTagStore((s) => s.tags)
  const [busy, setBusy] = useState(false)

  const bulkJob = useActivityStore((s) => s.jobs.find((j) => j.action === 'bulk_enrich'))
  const refreshActivity = useActivityStore((s) => s.refresh)
  const bulkRunning = bulkJob ? isActive(bulkJob) : false
  const [bulkError, setBulkError] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  const ids = useMemo(() => [...selected], [selected])

  /** Tags present on at least one selected asset, with how many carry each. */
  const onSelection = useMemo(() => {
    const counts = new Map<string, { tag: Tag; count: number }>()
    assets.forEach((asset) => {
      if (!selected.has(asset.id)) return
      asset.tags.forEach((tag) => {
        const entry = counts.get(tag.id)
        if (entry) entry.count += 1
        else counts.set(tag.id, { tag, count: 1 })
      })
    })
    return [...counts.values()].sort((a, b) => a.tag.name.localeCompare(b.tag.name))
  }, [assets, selected])

  const enrich = async (action: BulkAction) => {
    setBulkError(null)
    setUnavailable(false)
    try {
      await enrichmentApi.bulk(action, ids)
      await refreshActivity()
    } catch (err) {
      const code = apiErrorCode(err)
      // Both "not configured" codes are signposts rather than errors — the feature is
      // off and Settings is one click away.
      if (code === 'provider_unavailable' || code === 'embedding_unavailable') {
        setUnavailable(true)
      } else {
        setBulkError(apiErrorMessage(err, 'Could not start that'))
      }
    }
  }

  const run = async (add: string[], remove: string[]) => {
    setBusy(true)
    try {
      await applyTags(ids, add, remove)
    } catch {
      // The store restores the previous rows and surfaces the message; the selection
      // stays so the action can be retried without rebuilding it.
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sticky top-2 z-30 space-y-2 rounded-lg border border-blue-300 bg-blue-50/95 p-3 backdrop-blur dark:border-blue-800 dark:bg-blue-950/80">
      {/* Its own row rather than inline: the controls below carry labels, and a count
          sharing their row lands on a different baseline at every width. */}
      <p className="text-sm font-medium text-blue-900 dark:text-blue-100">
        {selected.size} selected
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[220px] flex-1">
          <TagInput
            tags={[]}
            suggestions={suggestions}
            onAdd={(names) => void run(names, [])}
            onRemove={() => {}}
            disabled={busy}
            label="Add a tag to all of them"
            placeholder="Type a tag and press Enter…"
          />
        </div>

        {onSelection.length > 0 && (
          <div>
            <label className="label" htmlFor="bulk-remove">
              Remove a tag
            </label>
            <select
              id="bulk-remove"
              className="input"
              value=""
              disabled={busy}
              onChange={(e) => {
                if (e.target.value) void run([], [e.target.value])
              }}
            >
              <option value="">Choose a tag…</option>
              {onSelection.map(({ tag, count }) => (
                <option key={tag.id} value={tag.id}>
                  {tag.name} ({count})
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="flex gap-2 pb-0.5">
          <button
            type="button"
            onClick={onSelectAll}
            className="btn btn-secondary px-3 py-1.5 text-xs"
          >
            Select all loaded
          </button>
          <button
            type="button"
            onClick={onClear}
            className="btn btn-ghost px-3 py-1.5 text-xs"
          >
            <X className="h-3.5 w-3.5" />
            Clear
          </button>
        </div>
      </div>

      <div className="space-y-1.5 border-t border-blue-200 pt-2 dark:border-blue-900">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-gray-600 dark:text-gray-400">
            {bulkRunning ? bulkJob?.stage || 'Working…' : 'Enrich these with AI:'}
          </span>
          {ENRICHMENTS.map(({ action, label, icon: Icon }) => (
            <button
              key={action}
              type="button"
              className="btn btn-ghost px-2.5 py-1 text-xs"
              disabled={bulkRunning || busy}
              onClick={() => void enrich(action)}
            >
              <Icon className="mr-1 h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        {bulkRunning && bulkJob?.detail && (
          <p className="text-xs text-gray-600 dark:text-gray-400">{bulkJob.detail}</p>
        )}
        {unavailable && (
          <p className="text-xs text-gray-600 dark:text-gray-400">
            That needs a provider.{' '}
            <Link
              to="/settings"
              className="text-blue-700 hover:underline dark:text-blue-400"
            >
              Add one in Settings
            </Link>
            .
          </p>
        )}
        {!bulkRunning && bulkJob?.status === 'error' && bulkJob.error_message && (
          <p className="text-xs text-red-700 dark:text-red-400">
            {bulkJob.error_message}
          </p>
        )}
        {bulkError && (
          <p className="text-xs text-red-700 dark:text-red-400">{bulkError}</p>
        )}
      </div>
    </div>
  )
}
