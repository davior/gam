import { useEffect, useMemo, useState } from 'react'
import { Search, SlidersHorizontal, X } from 'lucide-react'
import { buildCategoryTree, type CategoryNode } from '@/api/tags'
import TypeFilterChips, { Chip } from '@/components/TypeFilterChips'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'
import { formatDuration } from '@/utils/format'

/**
 * Everything that narrows the library.
 *
 * The less-used filters fold away, but an *active* filter never does: each one also
 * renders as a removable chip in the row below, so a folded panel can never silently
 * be the reason the grid looks empty.
 */

/** Mirrors `backend/app/ingest/filetypes.py`; a source the server never writes is noise. */
const SOURCES: Array<{ value: string; label: string }> = [
  { value: 'local_upload', label: 'Uploaded' },
  { value: 'url', label: 'From a URL' },
  { value: 'ai_generated', label: 'AI generated' },
  { value: 'gvc_export', label: 'Video Creator' },
]

/** An active filter, with the ✕ that clears just it. */
function ActiveChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 py-1 pl-3 pr-1.5 text-xs font-medium text-blue-800 dark:bg-blue-900/50 dark:text-blue-200">
      {label}
      <button
        type="button"
        onClick={onClear}
        className="rounded-full p-0.5 hover:bg-blue-200 dark:hover:bg-blue-800"
        aria-label={`Clear ${label}`}
      >
        <X className="h-3 w-3" />
      </button>
    </span>
  )
}

/** Flatten the tree for a `<select>`, keeping depth as indentation. */
function flatten(nodes: CategoryNode[], depth = 0): Array<{ id: string; label: string }> {
  return nodes.flatMap((node) => [
    { id: node.id, label: `${' '.repeat(depth * 2)}${node.name}` },
    ...flatten(node.children, depth + 1),
  ])
}

export default function FilterBar() {
  const query = useLibraryStore((s) => s.query)
  const typeFilter = useLibraryStore((s) => s.typeFilter)
  const tagFilter = useLibraryStore((s) => s.tagFilter)
  const categoryFilter = useLibraryStore((s) => s.categoryFilter)
  const sourceFilter = useLibraryStore((s) => s.sourceFilter)
  const minDuration = useLibraryStore((s) => s.minDuration)
  const maxDuration = useLibraryStore((s) => s.maxDuration)
  const uploadedAfter = useLibraryStore((s) => s.uploadedAfter)
  const uploadedBefore = useLibraryStore((s) => s.uploadedBefore)

  const setQuery = useLibraryStore((s) => s.setQuery)
  const setTypeFilter = useLibraryStore((s) => s.setTypeFilter)
  const toggleTag = useLibraryStore((s) => s.toggleTag)
  const setCategoryFilter = useLibraryStore((s) => s.setCategoryFilter)
  const setSourceFilter = useLibraryStore((s) => s.setSourceFilter)
  const setDurationRange = useLibraryStore((s) => s.setDurationRange)
  const setUploadedRange = useLibraryStore((s) => s.setUploadedRange)
  const clearFilters = useLibraryStore((s) => s.clearFilters)

  const tags = useTagStore((s) => s.tags)
  const categories = useTagStore((s) => s.categories)
  const loadTags = useTagStore((s) => s.ensureLoaded)

  const [showMore, setShowMore] = useState(false)
  // Held locally so a half-typed range is not fired at the server on every keystroke —
  // "1" on the way to "120" is a filter that hides almost everything for a moment.
  const [minDraft, setMinDraft] = useState('')
  const [maxDraft, setMaxDraft] = useState('')

  useEffect(() => {
    loadTags()
  }, [loadTags])

  useEffect(() => {
    setMinDraft(minDuration === null ? '' : String(minDuration))
    setMaxDraft(maxDuration === null ? '' : String(maxDuration))
  }, [minDuration, maxDuration])

  // Derived here rather than read off the store: a store accessor is a stable function
  // reference, so a memo keyed on it would never see a new category arrive.
  const categoryOptions = useMemo(
    () => flatten(buildCategoryTree(categories)),
    [categories]
  )
  const categoryName = categories.find((c) => c.id === categoryFilter)?.name

  const parsed = (value: string): number | null => {
    const n = Number(value)
    return value.trim() === '' || Number.isNaN(n) ? null : n
  }
  const min = parsed(minDraft)
  const max = parsed(maxDraft)
  // The server answers an inverted range with a 400. Saying so here is the difference
  // between a hint under the field and a red error bar over the grid.
  const rangeInverted = min !== null && max !== null && min > max

  const applyRange = () => {
    if (rangeInverted) return
    if (min !== minDuration || max !== maxDuration) setDurationRange(min, max)
  }

  /**
   * Commit only when focus leaves *both* duration fields.
   *
   * Per-field blur looked equivalent and was not: tabbing from "100" to the max field
   * committed `min=100` on its own, and then typing "10" was refused as an inverted
   * range — leaving a live duration filter on screen underneath a message saying the
   * range was invalid. Moving between the two halves of one control is not finishing
   * with it.
   */
  const applyRangeOnExit = (event: React.FocusEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
    applyRange()
  }

  const activeCount =
    tagFilter.length +
    (categoryFilter ? 1 : 0) +
    (sourceFilter ? 1 : 0) +
    (minDuration !== null || maxDuration !== null ? 1 : 0) +
    (uploadedAfter || uploadedBefore ? 1 : 0)
  const anyActive = activeCount > 0 || Boolean(query.trim()) || Boolean(typeFilter)

  const durationLabel = () => {
    if (minDuration !== null && maxDuration !== null) {
      return `${formatDuration(minDuration)}–${formatDuration(maxDuration)}`
    }
    if (minDuration !== null) return `Longer than ${formatDuration(minDuration)}`
    return `Shorter than ${formatDuration(maxDuration)}`
  }

  const dateLabel = () => {
    if (uploadedAfter && uploadedBefore) return `${uploadedAfter} to ${uploadedBefore}`
    return uploadedAfter
      ? `Added after ${uploadedAfter}`
      : `Added before ${uploadedBefore}`
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input
            className="input pl-8"
            placeholder="Search your library…"
            aria-label="Search your library"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <TypeFilterChips value={typeFilter} onChange={setTypeFilter} />

        <button
          type="button"
          onClick={() => setShowMore((open) => !open)}
          aria-expanded={showMore}
          className="btn btn-secondary px-3 py-1.5 text-xs"
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
          More filters
          {activeCount > 0 && (
            <span className="rounded-full bg-blue-600 px-1.5 text-[11px] text-white">
              {activeCount}
            </span>
          )}
        </button>
      </div>

      {tags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-xs text-gray-500 dark:text-gray-400">Tags</span>
          {tags.slice(0, 12).map((tag) => (
            <Chip
              key={tag.id}
              active={tagFilter.some((t) => t.toLowerCase() === tag.name.toLowerCase())}
              onClick={() => toggleTag(tag.name)}
            >
              {tag.name}
              {tag.asset_count !== undefined && (
                <span className="ml-1 tabular-nums opacity-60">{tag.asset_count}</span>
              )}
            </Chip>
          ))}
        </div>
      )}

      {showMore && (
        <div className="grid gap-3 rounded-lg border border-gray-200 p-3 sm:grid-cols-2 lg:grid-cols-4 dark:border-gray-700">
          <div>
            <label className="label" htmlFor="filter-category">
              Category
            </label>
            <select
              id="filter-category"
              className="input"
              value={categoryFilter ?? ''}
              onChange={(e) => setCategoryFilter(e.target.value || null)}
            >
              <option value="">Any category</option>
              {categoryOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="label" htmlFor="filter-source">
              Source
            </label>
            <select
              id="filter-source"
              className="input"
              value={sourceFilter ?? ''}
              onChange={(e) => setSourceFilter(e.target.value || null)}
            >
              <option value="">Any source</option>
              {SOURCES.map((source) => (
                <option key={source.value} value={source.value}>
                  {source.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <span className="label">Duration (seconds)</span>
            <div className="flex items-center gap-1.5" onBlur={applyRangeOnExit}>
              <input
                className="input min-w-0"
                type="number"
                min={0}
                placeholder="Min"
                aria-label="Minimum duration in seconds"
                value={minDraft}
                onChange={(e) => setMinDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && applyRange()}
              />
              <span className="text-xs text-gray-400">to</span>
              <input
                className="input min-w-0"
                type="number"
                min={0}
                placeholder="Max"
                aria-label="Maximum duration in seconds"
                value={maxDraft}
                onChange={(e) => setMaxDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && applyRange()}
              />
            </div>
            {rangeInverted && (
              <p role="alert" className="mt-1 text-[11px] text-red-600 dark:text-red-400">
                The minimum is longer than the maximum.
              </p>
            )}
          </div>

          <div>
            <span className="label">Added</span>
            <div className="flex items-center gap-1.5">
              <input
                className="input min-w-0"
                type="date"
                aria-label="Added after"
                value={uploadedAfter ?? ''}
                onChange={(e) => setUploadedRange(e.target.value || null, uploadedBefore)}
              />
              <span className="text-xs text-gray-400">to</span>
              <input
                className="input min-w-0"
                type="date"
                aria-label="Added before"
                value={uploadedBefore ?? ''}
                onChange={(e) => setUploadedRange(uploadedAfter, e.target.value || null)}
              />
            </div>
          </div>
        </div>
      )}

      {anyActive && (
        <div className="flex flex-wrap items-center gap-1.5">
          {tagFilter.map((name) => (
            <ActiveChip key={name} label={name} onClear={() => toggleTag(name)} />
          ))}
          {categoryFilter && (
            <ActiveChip
              label={categoryName ?? 'Category'}
              onClear={() => setCategoryFilter(null)}
            />
          )}
          {sourceFilter && (
            <ActiveChip
              label={SOURCES.find((s) => s.value === sourceFilter)?.label ?? sourceFilter}
              onClear={() => setSourceFilter(null)}
            />
          )}
          {(minDuration !== null || maxDuration !== null) && (
            <ActiveChip
              label={durationLabel()}
              onClear={() => setDurationRange(null, null)}
            />
          )}
          {(uploadedAfter || uploadedBefore) && (
            <ActiveChip
              label={dateLabel()}
              onClear={() => setUploadedRange(null, null)}
            />
          )}
          <button
            type="button"
            onClick={clearFilters}
            className="px-2 py-1 text-xs text-gray-500 underline hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}
