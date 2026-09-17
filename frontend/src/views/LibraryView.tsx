import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Library, X } from 'lucide-react'
import type { Asset } from '@/api/assets'
import AssetCard from '@/components/AssetCard'
import AssetDetail from '@/components/AssetDetail'
import DetailDock from '@/components/DetailDock'
import FilterBar from '@/components/FilterBar'
import SelectionBar from '@/components/SelectionBar'
import UploadZone from '@/components/UploadZone'
import { useLibraryStore } from '@/stores/library'

export default function LibraryView() {
  const assets = useLibraryStore((s) => s.assets)
  const total = useLibraryStore((s) => s.total)
  const loading = useLibraryStore((s) => s.loading)
  const loadingMore = useLibraryStore((s) => s.loadingMore)
  const error = useLibraryStore((s) => s.error)
  const query = useLibraryStore((s) => s.query)
  const typeFilter = useLibraryStore((s) => s.typeFilter)
  const tagFilter = useLibraryStore((s) => s.tagFilter)
  const categoryFilter = useLibraryStore((s) => s.categoryFilter)
  const sourceFilter = useLibraryStore((s) => s.sourceFilter)
  const rejections = useLibraryStore((s) => s.rejections)

  const load = useLibraryStore((s) => s.load)
  const loadMore = useLibraryStore((s) => s.loadMore)
  const dismissRejections = useLibraryStore((s) => s.dismissRejections)

  const [openId, setOpenId] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  // Where a Shift-click range starts. Held in a ref: it steers the next click but
  // nothing renders from it.
  const anchorRef = useRef<string | null>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void load()
  }, [load])

  // Infinite scroll. An observer rather than a scroll handler: it does not fire on
  // every pixel, and it keeps working if the grid is put in a different container.
  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel) return

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) void loadMore()
      },
      // Start fetching before the sentinel is actually visible, so the next page is
      // usually there by the time the user reaches it.
      { rootMargin: '400px' }
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [loadMore])

  // Drop ids that are no longer on screen — a filter change can retire half a
  // selection, and bulk-tagging rows the user can no longer see is not what they asked
  // for.
  useEffect(() => {
    setSelected((current) => {
      if (current.size === 0) return current
      const visible = new Set(assets.map((a) => a.id))
      const kept = [...current].filter((id) => visible.has(id))
      return kept.length === current.size ? current : new Set(kept)
    })
  }, [assets])

  const toggle = useCallback((id: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    anchorRef.current = id
  }, [])

  const selectRangeTo = useCallback(
    (id: string) => {
      const anchor = anchorRef.current
      const order = assets.map((a) => a.id)
      const from = anchor ? order.indexOf(anchor) : -1
      const to = order.indexOf(id)
      if (from === -1 || to === -1) {
        toggle(id)
        return
      }
      const [start, end] = from <= to ? [from, to] : [to, from]
      setSelected((current) => {
        const next = new Set(current)
        order.slice(start, end + 1).forEach((each) => next.add(each))
        return next
      })
    },
    [assets, toggle]
  )

  const activate = useCallback(
    (asset: Asset, event: React.MouseEvent) => {
      if (event.shiftKey && selected.size > 0) {
        selectRangeTo(asset.id)
        return
      }
      // Ctrl/⌘ starts a selection; once one exists a plain click continues it, because
      // holding a modifier for every one of fifty cards is not a workflow — and on a
      // touchscreen there is no modifier at all.
      if (event.ctrlKey || event.metaKey || selected.size > 0) {
        toggle(asset.id)
        return
      }
      setOpenId(asset.id)
    },
    [selected.size, selectRangeTo, toggle]
  )

  const selectAll = useCallback(() => {
    setSelected(new Set(assets.map((a) => a.id)))
  }, [assets])

  const clearSelection = useCallback(() => {
    setSelected(new Set())
    anchorRef.current = null
  }, [])

  // Read from the live list so an edit made in the panel is reflected behind it.
  const openAsset = useMemo(
    () => assets.find((a) => a.id === openId) ?? null,
    [assets, openId]
  )

  const filtering = Boolean(
    query.trim() || typeFilter || tagFilter.length > 0 || categoryFilter || sourceFilter
  )

  return (
    <div className="flex h-full min-h-0">
      <div className="min-w-0 flex-1 overflow-auto">
        {/* A container query context rather than a max width. What the grid has to
            work with is whatever is left beside the detail panel, and the viewport
            stops describing that the moment the panel is open and drag-resizable. */}
        <div className="@container space-y-4 px-4 py-4">
          <UploadZone />

          {rejections.length > 0 && (
            <div className="flex items-start justify-between gap-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs dark:border-amber-800 dark:bg-amber-950/30">
              <div className="space-y-0.5">
                <p className="font-medium text-amber-900 dark:text-amber-200">
                  {rejections.length} file{rejections.length === 1 ? '' : 's'} could not
                  be added
                </p>
                {rejections.map((rejection) => (
                  <p
                    key={rejection.filename}
                    className="text-amber-800 dark:text-amber-300"
                  >
                    {rejection.filename} — {rejection.message}
                  </p>
                ))}
              </div>
              <button
                type="button"
                onClick={dismissRejections}
                className="btn btn-ghost p-1"
                aria-label="Dismiss"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {error && (
            <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
              {error}
            </p>
          )}

          <FilterBar />

          {selected.size > 0 && (
            <SelectionBar
              selected={selected}
              assets={assets}
              onSelectAll={selectAll}
              onClear={clearSelection}
            />
          )}

          {loading ? (
            <p className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
              Loading…
            </p>
          ) : assets.length === 0 ? (
            <EmptyState filtering={filtering} />
          ) : (
            <>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {total} asset{total === 1 ? '' : 's'}
                </p>
                {/* Ctrl/⌘-click announces itself nowhere, so it is said out loud once. */}
                {selected.size === 0 && (
                  <p className="text-xs text-gray-400 dark:text-gray-500">
                    Ctrl/⌘-click to select several, then tag them at once
                  </p>
                )}
              </div>
              {/* auto-fill, not auto-fit: a half-empty last row stays left-aligned
              instead of stretching two tiles across the whole screen. Columns are
              added as room appears rather than at four fixed viewport widths. */}
              <div className="grid justify-items-center gap-3 grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] @2xl:grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] @5xl:grid-cols-[repeat(auto-fill,minmax(18rem,1fr))]">
                {assets.map((asset) => (
                  <AssetCard
                    key={asset.id}
                    asset={asset}
                    selected={selected.has(asset.id)}
                    onActivate={activate}
                  />
                ))}
              </div>
              <div ref={sentinelRef} className="h-4" />
              {loadingMore && (
                <p className="pb-4 text-center text-xs text-gray-500 dark:text-gray-400">
                  Loading more…
                </p>
              )}
            </>
          )}
        </div>
      </div>

      {openAsset && (
        <DetailDock label={openAsset.name} onClose={() => setOpenId(null)}>
          <AssetDetail asset={openAsset} onClose={() => setOpenId(null)} />
        </DetailDock>
      )}
    </div>
  )
}

function EmptyState({ filtering }: { filtering: boolean }) {
  return (
    <div className="flex flex-col items-center gap-2 py-16 text-center">
      <Library className="h-9 w-9 text-gray-400 dark:text-gray-500" />
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        {filtering ? 'Nothing matches that' : 'Your library is empty'}
      </h2>
      <p className="max-w-sm text-sm text-gray-600 dark:text-gray-400">
        {filtering
          ? 'Try a different search, or clear the filters.'
          : 'Drop some files above to get started. You can name and describe them later.'}
      </p>
    </div>
  )
}
