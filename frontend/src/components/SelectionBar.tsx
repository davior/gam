import { useMemo, useState } from 'react'
import { X } from 'lucide-react'
import type { Asset } from '@/api/assets'
import type { Tag } from '@/api/tags'
import TagInput from '@/components/TagInput'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'

/**
 * Bulk tagging for the current selection.
 *
 * The remove side lists only tags that are actually on something selected — offering
 * the whole catalogue would make "remove" mostly a list of no-ops, and hide the two
 * entries that would do anything.
 */

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
    </div>
  )
}
