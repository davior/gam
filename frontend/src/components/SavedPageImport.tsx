import { useMemo, useState } from 'react'
import { FileDown, Loader2 } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import {
  importsApi,
  MAX_TRACKS_PER_IMPORT,
  type SavedFileResult,
  type SavedPagePreview,
} from '@/api/imports'
import { useLibraryStore } from '@/stores/library'
import { matchFiles } from '@/utils/matchFiles'

/**
 * Tracks from a site that refuses GAM's own requests — Pixabay answers a server with 403.
 *
 * The person does the part that needs a browser: open the page, save it, download the
 * tracks they want. GAM reads the saved page for each track's details and matches the
 * downloads to it, and never contacts the site. Matching is shown before uploading, from
 * a mirror of the server's rule, so a wrong or missing match is seen while it can still be
 * fixed by renaming a file; the server's own match is the one that counts.
 */

const RESULT_LABELS: Record<SavedFileResult['status'], string> = {
  imported: 'Imported',
  duplicate: 'Already in library',
  unmatched: 'Not on the page',
  rejected: 'Not imported',
}

export default function SavedPageImport() {
  const reload = useLibraryStore((s) => s.load)

  const [page, setPage] = useState<File | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [preview, setPreview] = useState<SavedPagePreview | null>(null)
  const [reading, setReading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [results, setResults] = useState<SavedFileResult[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const matches = useMemo(
    () =>
      preview
        ? matchFiles(
            preview.tracks,
            files.map((f) => f.name)
          )
        : new Map(),
    [preview, files]
  )
  const tooMany = files.length > MAX_TRACKS_PER_IMPORT

  const choosePage = async (file: File | undefined) => {
    setPage(file ?? null)
    setPreview(null)
    setResults(null)
    setError(null)
    if (!file) return
    setReading(true)
    try {
      setPreview(await importsApi.previewSavedPage(file))
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not read that saved page'))
    } finally {
      setReading(false)
    }
  }

  const startImport = async () => {
    if (!page || files.length === 0 || tooMany || importing) return
    setImporting(true)
    setError(null)
    try {
      const done = await importsApi.importSavedPage(page, files)
      setResults(done.results)
      setFiles([])
      void reload()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not import the files'))
    } finally {
      setImporting(false)
    }
  }

  return (
    <div
      className="space-y-2 rounded-lg border border-gray-200 px-3 py-2.5 dark:border-gray-700"
      role="group"
      aria-label="Import from a saved page"
    >
      <p className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
        <FileDown className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
        Or use a saved page, for sites that block GAM (Pixabay…)
      </p>
      <ol className="list-decimal space-y-0.5 pl-10 text-xs text-gray-600 dark:text-gray-400">
        <li>Open the page in your browser and save it (File → Save Page As).</li>
        <li>Download the tracks you want with the site&rsquo;s Download button.</li>
        <li>Choose both here. GAM fills in each track&rsquo;s details from the page.</li>
      </ol>

      <div className="grid gap-2 pl-6 sm:grid-cols-2">
        <label className="text-xs text-gray-600 dark:text-gray-400">
          Saved page
          <input
            type="file"
            accept=".html,.htm,text/html"
            className="mt-1 block w-full text-xs"
            onChange={(e) => void choosePage(e.target.files?.[0])}
          />
        </label>
        <label className="text-xs text-gray-600 dark:text-gray-400">
          Downloaded tracks
          <input
            type="file"
            accept="audio/*"
            multiple
            className="mt-1 block w-full text-xs"
            onChange={(e) => {
              setFiles(Array.from(e.target.files ?? []))
              setResults(null)
            }}
          />
        </label>
      </div>

      {reading && (
        <p className="pl-6 text-xs text-gray-500">
          <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
          Reading the page…
        </p>
      )}
      {preview && (
        <p className="pl-6 text-xs text-gray-600 dark:text-gray-400">
          {preview.tracks.length} track{preview.tracks.length === 1 ? '' : 's'} on the
          page
          {preview.note ? `. ${preview.note}` : '.'}
        </p>
      )}

      {preview && files.length > 0 && (
        <ul
          className="max-h-60 space-y-0.5 overflow-auto pl-6"
          aria-label="Files and their tracks"
        >
          {files.map((file) => {
            const track = matches.get(file.name)
            return (
              <li key={file.name} className="flex gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-300">
                  {file.name}
                </span>
                {track ? (
                  <span className="shrink-0 text-gray-500">
                    → {track.title}
                    {track.creator ? ` · ${track.creator}` : ''}
                  </span>
                ) : (
                  <span className="shrink-0 text-amber-700 dark:text-amber-400">
                    Not on the page
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {tooMany && (
        <p className="pl-6 text-xs text-red-600 dark:text-red-400">
          Choose at most {MAX_TRACKS_PER_IMPORT} files at a time.
        </p>
      )}

      {page && files.length > 0 && (
        <div className="pl-6">
          <button
            type="button"
            className="btn btn-primary px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
            onClick={() => void startImport()}
            disabled={importing || tooMany}
          >
            {importing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              `Import ${files.length} file${files.length === 1 ? '' : 's'}`
            )}
          </button>
        </div>
      )}

      {results && (
        <ul className="space-y-0.5 pl-6" aria-label="Import results">
          {results.map((result, index) => (
            <li key={`${result.filename}-${index}`} className="text-xs">
              <span className="font-medium text-gray-800 dark:text-gray-200">
                {RESULT_LABELS[result.status]}
              </span>
              <span className="text-gray-500">
                {' '}
                · {result.track_title ?? result.filename}
                {result.message ? ` — ${result.message}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p role="alert" className="pl-6 text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  )
}
