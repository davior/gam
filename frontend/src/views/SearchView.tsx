import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Search, Sparkles, TriangleAlert } from 'lucide-react'
import { searchApi, splitHighlights, type SearchHit } from '@/api/search'
import { apiErrorMessage } from '@/api/client'
import AssetDetail from '@/components/AssetDetail'
import AssetThumb from '@/components/AssetThumb'
import { formatDuration } from '@/utils/format'
import type { Asset } from '@/api/assets'

/** Long enough that typing does not fire a request per keystroke, short enough that
 *  results feel like they are keeping up. */
const DEBOUNCE_MS = 250

export default function SearchView() {
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''

  const [draft, setDraft] = useState(query)
  const [hits, setHits] = useState<SearchHit[]>([])
  const [semantic, setSemantic] = useState(false)
  const [semanticError, setSemanticError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  const [open, setOpen] = useState<{ asset: Asset; at: number | null } | null>(null)

  // Bumped per request; a response tagged with an older value is discarded. Typing
  // fires several, and a slow early one landing after a fast later one would replace
  // the results the user is reading.
  const token = useRef(0)

  const run = useCallback(async (q: string) => {
    const mine = ++token.current
    if (!q.trim()) {
      setHits([])
      setSearched(false)
      return
    }

    setLoading(true)
    try {
      const response = await searchApi.run(q)
      if (mine !== token.current) return
      setHits(response.data)
      setSemantic(response.semantic)
      setSemanticError(response.semantic_error)
      setError(null)
      setSearched(true)
    } catch (err) {
      if (mine !== token.current) return
      setError(apiErrorMessage(err, 'Search failed'))
    } finally {
      if (mine === token.current) setLoading(false)
    }
  }, [])

  // Keep the URL in step so a search can be linked to or reloaded.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (draft !== query) {
        setParams(draft.trim() ? { q: draft } : {}, { replace: true })
      }
      void run(draft)
    }, DEBOUNCE_MS)
    return () => clearTimeout(timer)
    // `query` deliberately omitted: including it would re-run on the URL update this
    // effect itself causes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, run, setParams])

  return (
    <div className="mx-auto max-w-4xl space-y-4 px-4 py-4">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          className="input pl-9 text-base"
          placeholder="What are you looking for?"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          autoFocus
        />
      </div>

      <p className="text-xs text-gray-500 dark:text-gray-400">
        Searches names, descriptions and every spoken word.{' '}
        {semantic ? (
          <span className="inline-flex items-center gap-1 text-gray-600 dark:text-gray-300">
            <Sparkles className="h-3 w-3" />
            Meaning-based search is on, so you need not remember the exact wording.
          </span>
        ) : (
          <span>
            Add an embedding provider in Settings to search by meaning as well as by
            words.
          </span>
        )}
      </p>

      {semanticError && (
        <p className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Meaning-based search is unavailable, so these are word matches only.{' '}
            {semanticError}
          </span>
        </p>
      )}

      {error && (
        <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </p>
      )}

      {loading && hits.length === 0 && (
        <p className="py-10 text-center text-sm text-gray-500 dark:text-gray-400">
          Searching…
        </p>
      )}

      {searched && !loading && hits.length === 0 && (
        <div className="py-12 text-center">
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
            Nothing matched “{query}”
          </p>
          <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
            {semantic
              ? 'Try describing it differently, or check the asset has been transcribed.'
              : 'Only exact words are searched right now. Adding an embedding provider in Settings would let you describe it instead.'}
          </p>
        </div>
      )}

      {hits.length > 0 && (
        <ol className="space-y-2">
          {hits.map((hit) => (
            <ResultRow
              key={hit.asset.id}
              hit={hit}
              onOpen={() => setOpen({ asset: hit.asset, at: hit.start_time })}
            />
          ))}
        </ol>
      )}

      {open && (
        <AssetDetail
          asset={open.asset}
          startAt={open.at ?? undefined}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}

function ResultRow({ hit, onOpen }: { hit: SearchHit; onOpen: () => void }) {
  const parts = splitHighlights(hit.snippet)
  const timestamp = hit.start_time !== null ? formatDuration(hit.start_time) : ''

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="card flex w-full gap-3 p-2.5 text-left transition-colors hover:bg-gray-50 dark:hover:bg-gray-800/60"
      >
        <AssetThumb asset={hit.asset} className="h-14 w-20 shrink-0 rounded" />

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
              {hit.asset.name}
            </p>
            {timestamp && (
              <span className="shrink-0 font-mono text-[11px] tabular-nums text-blue-600 dark:text-blue-400">
                {timestamp}
              </span>
            )}
            {hit.sources.includes('semantic') && !hit.sources.includes('keyword') && (
              <span
                className="shrink-0 text-[10px] text-gray-400"
                title="Found by meaning rather than by matching words"
              >
                similar
              </span>
            )}
          </div>

          {parts.length > 0 && (
            <p className="mt-0.5 line-clamp-2 text-xs text-gray-600 dark:text-gray-400">
              {parts.map((part, index) =>
                part.match ? (
                  <mark
                    key={index}
                    className="bg-yellow-200 text-gray-900 dark:bg-yellow-500/30 dark:text-gray-100"
                  >
                    {part.text}
                  </mark>
                ) : (
                  <span key={index}>{part.text}</span>
                )
              )}
            </p>
          )}

          {hit.other_matches > 0 && (
            <p className="mt-0.5 text-[11px] text-gray-500 dark:text-gray-500">
              and {hit.other_matches} more moment{hit.other_matches === 1 ? '' : 's'} in
              this asset
            </p>
          )}
        </div>
      </button>
    </li>
  )
}
