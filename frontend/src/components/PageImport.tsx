import { useMemo, useState, type FormEvent } from 'react'
import { ExternalLink, ListMusic, Loader2 } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import {
  importsApi,
  MAX_TRACKS_PER_IMPORT,
  type PagePreview,
  type PageTrack,
} from '@/api/imports'
import { useActivityStore } from '@/stores/activity'

/**
 * Choose tracks from a music library's page — incompetech, Mixkit, anything that lists
 * music — and import the chosen ones with what the page says about each.
 *
 * Two steps where `UrlImport` has one, because a page can list a thousand tracks and the
 * person wants a dozen: Find reads the page and lists them, nothing is downloaded until
 * Import, and only what is ticked is. The filter is what makes incompetech's whole
 * catalogue (one page) usable; it searches the details too, so "dark piano" narrows to
 * tracks whose moods and instruments say so.
 *
 * Duplicates are caught three ways. "In library" is a track already imported from this
 * URL and cannot be ticked. "Maybe in library" is a title (and artist) the library
 * already has, probably the same piece from elsewhere; it can still be ticked. And the
 * import job itself compares the downloaded file's checksum with the library, so the
 * same file under another URL, or one uploaded by hand, is never stored twice.
 *
 * Details are corrected after import, in the asset's Music and Source tabs, rather than
 * here: the review is for choosing, and an edit form per row would bury the list.
 */

const SOURCE_LABELS: Record<string, string> = {
  incompetech: "incompetech's catalogue data",
  mixkit: "Mixkit's track data",
  'schema.org': "the page's structured track data",
  AI: 'the AI reading the page (check the details)',
  links: 'the audio links on the page (titles only)',
}

function clock(seconds: number | null): string {
  if (!seconds) return ''
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

function summary(track: PageTrack): string {
  const words = [...track.genres, ...track.moods, ...track.instruments]
  const parts = []
  if (words.length) parts.push(words.join(', '))
  if (track.bpm) parts.push(`${Math.round(track.bpm)} BPM`)
  return parts.join(' · ')
}

function matches(track: PageTrack, query: string): boolean {
  const haystack = [
    track.title,
    track.creator ?? '',
    track.source_title ?? '',
    ...track.genres,
    ...track.moods,
    ...track.instruments,
    ...track.tags,
  ]
    .join(' ')
    .toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => haystack.includes(word))
}

export default function PageImport() {
  const refreshActivity = useActivityStore((s) => s.refresh)

  const [url, setUrl] = useState('')
  const [useAi, setUseAi] = useState(true)
  const [finding, setFinding] = useState(false)
  const [importing, setImporting] = useState(false)
  const [preview, setPreview] = useState<PagePreview | null>(null)
  const [filter, setFilter] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [queued, setQueued] = useState<number | null>(null)

  const shown = useMemo(
    () => (preview ? preview.tracks.filter((t) => matches(t, filter)) : []),
    [preview, filter]
  )
  const full = chosen.size >= MAX_TRACKS_PER_IMPORT

  const find = async (event: FormEvent) => {
    event.preventDefault()
    const link = url.trim()
    if (!link || finding) return
    setFinding(true)
    setError(null)
    setQueued(null)
    try {
      const found = await importsApi.previewPage(link, useAi)
      setPreview(found)
      setChosen(new Set())
      setFilter('')
    } catch (err) {
      setPreview(null)
      setError(apiErrorMessage(err, 'Could not read that page'))
    } finally {
      setFinding(false)
    }
  }

  const toggle = (track: PageTrack) =>
    setChosen((previous) => {
      const next = new Set(previous)
      if (next.has(track.audio_url)) next.delete(track.audio_url)
      else if (next.size < MAX_TRACKS_PER_IMPORT) next.add(track.audio_url)
      return next
    })

  const chooseShown = () =>
    setChosen((previous) => {
      const next = new Set(previous)
      for (const track of shown) {
        if (next.size >= MAX_TRACKS_PER_IMPORT) break
        // Skips the likely duplicates too: ticking one of those is a choice to make one
        // at a time, not in bulk.
        if (!track.existing_asset_id && !track.similar_asset_id) next.add(track.audio_url)
      }
      return next
    })

  const startImport = async () => {
    if (!preview || chosen.size === 0 || importing) return
    setImporting(true)
    setError(null)
    try {
      const tracks = preview.tracks.filter((t) => chosen.has(t.audio_url))
      const jobs = await importsApi.importTracks(tracks)
      setQueued(jobs.length)
      setChosen(new Set())
      void refreshActivity()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not start the import'))
    } finally {
      setImporting(false)
    }
  }

  return (
    <div
      className="space-y-2 rounded-lg border border-gray-200 px-3 py-2.5 dark:border-gray-700"
      aria-label="Import tracks from a page"
      role="group"
    >
      <form onSubmit={(e) => void find(e)} className="flex items-center gap-2">
        <ListMusic className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
        <input
          className="input min-w-0 flex-1"
          type="text"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="Or paste a page of music tracks (incompetech, Mixkit…)"
          aria-label="Page of tracks"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button
          type="submit"
          className="btn btn-primary px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!url.trim() || finding}
        >
          {finding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Find tracks'}
        </button>
      </form>
      <label className="flex items-center gap-1.5 pl-6 text-xs text-gray-600 dark:text-gray-400">
        <input
          type="checkbox"
          checked={useAi}
          onChange={(e) => setUseAi(e.target.checked)}
        />
        Ask the AI when the page has no track data (costs a little)
      </label>

      {error && (
        <p role="alert" className="pl-6 text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {preview && (
        <div className="space-y-2 pl-6">
          <p className="text-xs text-gray-600 dark:text-gray-400">
            {preview.tracks.length} track{preview.tracks.length === 1 ? '' : 's'} found,
            from {SOURCE_LABELS[preview.source] ?? preview.source}.
          </p>
          {preview.note && (
            <p className="text-xs text-amber-700 dark:text-amber-400">{preview.note}</p>
          )}

          {preview.tracks.length > 0 && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  className="input min-w-0 flex-1 text-xs"
                  type="search"
                  placeholder="Filter by title, genre, mood, instrument…"
                  aria-label="Filter tracks"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn-secondary px-2 py-1 text-xs"
                  onClick={chooseShown}
                  disabled={full}
                >
                  Tick shown
                </button>
              </div>

              <ul
                className="max-h-80 space-y-1 overflow-auto"
                aria-label="Tracks on the page"
              >
                {shown.map((track) => {
                  const inLibrary = Boolean(track.existing_asset_id)
                  const maybeInLibrary = !inLibrary && Boolean(track.similar_asset_id)
                  const ticked = chosen.has(track.audio_url)
                  return (
                    <li key={track.audio_url}>
                      <label
                        className={`flex items-start gap-2 rounded p-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-700/50 ${
                          inLibrary ? 'opacity-60' : ''
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          checked={ticked}
                          disabled={inLibrary || (full && !ticked)}
                          onChange={() => toggle(track)}
                          aria-label={track.title}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-baseline gap-2">
                            <span className="truncate font-medium text-gray-900 dark:text-gray-100">
                              {track.title}
                            </span>
                            {track.creator && (
                              <span className="truncate text-gray-500">
                                {track.creator}
                              </span>
                            )}
                            <span className="ml-auto shrink-0 text-gray-500">
                              {inLibrary ? (
                                'In library'
                              ) : maybeInLibrary ? (
                                <span
                                  className="text-amber-700 dark:text-amber-400"
                                  title="A track with this title is already in your library"
                                >
                                  Maybe in library
                                </span>
                              ) : (
                                clock(track.duration_seconds)
                              )}
                            </span>
                          </span>
                          {summary(track) && (
                            <span className="block truncate text-gray-500">
                              {summary(track)}
                            </span>
                          )}
                        </span>
                        <a
                          href={track.audio_url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="mt-0.5 shrink-0 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                          title="Listen on the site"
                          aria-label={`Listen to ${track.title} on the site`}
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      </label>
                    </li>
                  )
                })}
                {shown.length === 0 && (
                  <li className="text-xs text-gray-500">No track matches that filter.</li>
                )}
              </ul>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="btn btn-primary px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => void startImport()}
                  disabled={chosen.size === 0 || importing}
                >
                  {importing ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : chosen.size ? (
                    `Import ${chosen.size} track${chosen.size === 1 ? '' : 's'}`
                  ) : (
                    'Import tracks'
                  )}
                </button>
                {full && (
                  <span className="text-xs text-gray-500">
                    {MAX_TRACKS_PER_IMPORT} at a time.
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {queued !== null && (
        <p role="status" className="pl-6 text-xs text-green-700 dark:text-green-400">
          Queued {queued} track{queued === 1 ? '' : 's'}. Each is analysed for tempo, key
          and energy once it lands.
        </p>
      )}
    </div>
  )
}
