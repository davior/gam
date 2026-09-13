import { useEffect, useMemo, useRef, useState } from 'react'
import { Library, Search, X } from 'lucide-react'
import type { Asset, AssetType } from '@/api/assets'
import AssetCard from '@/components/AssetCard'
import AssetDetail from '@/components/AssetDetail'
import UploadZone from '@/components/UploadZone'
import { useLibraryStore } from '@/stores/library'

const TYPE_FILTERS: Array<{ value: AssetType | null; label: string }> = [
  { value: null, label: 'All' },
  { value: 'video', label: 'Video' },
  { value: 'image', label: 'Images' },
  { value: 'audio', label: 'Audio' },
  { value: 'document', label: 'Documents' },
]

export default function LibraryView() {
  const assets = useLibraryStore((s) => s.assets)
  const total = useLibraryStore((s) => s.total)
  const loading = useLibraryStore((s) => s.loading)
  const loadingMore = useLibraryStore((s) => s.loadingMore)
  const error = useLibraryStore((s) => s.error)
  const query = useLibraryStore((s) => s.query)
  const typeFilter = useLibraryStore((s) => s.typeFilter)
  const rejections = useLibraryStore((s) => s.rejections)

  const load = useLibraryStore((s) => s.load)
  const loadMore = useLibraryStore((s) => s.loadMore)
  const setQuery = useLibraryStore((s) => s.setQuery)
  const setTypeFilter = useLibraryStore((s) => s.setTypeFilter)
  const dismissRejections = useLibraryStore((s) => s.dismissRejections)

  const [openId, setOpenId] = useState<string | null>(null)
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

  // Read from the live list so an edit made in the panel is reflected behind it.
  const openAsset = useMemo(
    () => assets.find((a) => a.id === openId) ?? null,
    [assets, openId]
  )

  const filtering = Boolean(query.trim() || typeFilter)

  return (
    <div className="mx-auto max-w-7xl space-y-4 px-4 py-4">
      <UploadZone />

      {rejections.length > 0 && (
        <div className="flex items-start justify-between gap-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs dark:border-amber-800 dark:bg-amber-950/30">
          <div className="space-y-0.5">
            <p className="font-medium text-amber-900 dark:text-amber-200">
              {rejections.length} file{rejections.length === 1 ? '' : 's'} could not be
              added
            </p>
            {rejections.map((rejection) => (
              <p key={rejection.filename} className="text-amber-800 dark:text-amber-300">
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

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input
            className="input pl-8"
            placeholder="Search your library…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <div className="flex flex-wrap gap-1">
          {TYPE_FILTERS.map((filter) => (
            <button
              key={filter.label}
              type="button"
              onClick={() => setTypeFilter(filter.value)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                typeFilter === filter.value
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
          Loading…
        </p>
      ) : assets.length === 0 ? (
        <EmptyState filtering={filtering} />
      ) : (
        <>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {total} asset{total === 1 ? '' : 's'}
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
            {assets.map((asset) => (
              <AssetCard
                key={asset.id}
                asset={asset}
                onOpen={(a: Asset) => setOpenId(a.id)}
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

      {openAsset && <AssetDetail asset={openAsset} onClose={() => setOpenId(null)} />}
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
