import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { FileText, ImagePlus, Loader2, ScanText } from 'lucide-react'
import { enrichmentApi, type DocumentPage } from '@/api/enrichment'
import { documentsApi } from '@/api/documents'
import type { Asset } from '@/api/assets'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { apiErrorMessage } from '@/api/client'
import EnrichmentButton from '@/components/EnrichmentButton'
import { useActivityStore } from '@/stores/activity'

interface Props {
  assetId: string
  /** Only a PDF has pages to render. A .docx has none until something lays it out, and
   *  a slide or a worksheet is not a page of anything that can be photographed. */
  isPdf?: boolean
}

/** How often to poll a running extraction. Matches TranscriptPanel: fast enough to feel
 *  live, slow enough not to hammer the API for a job that takes a while. */
const POLL_MS = 2000

/**
 * What extraction read out of this document.
 *
 * Read-only, unlike `TranscriptPanel`: a transcript is corrected because speech
 * recognition mishears, and a person is the authority on what was said. Extracted text
 * is a faithful copy of bytes that are already in the file — an edit here would be an
 * edit to the document, made in the wrong place.
 *
 * It exists at all because text nobody can see is indistinguishable from text that was
 * never extracted, and this repository has had enough of that.
 */
export default function DocumentTextPanel({ assetId, isPdf = false }: Props) {
  const [pages, setPages] = useState<DocumentPage[]>([])
  const [job, setJob] = useState<ActivityJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Grabbing a page as a picture. The count comes from the file, not from the extracted
  // text: a scanned PDF has no text rows at all but still has pages to grab.
  const [pageCount, setPageCount] = useState<number | null>(null)
  const [pageInput, setPageInput] = useState('1')
  const [grabbing, setGrabbing] = useState<number | null>(null)
  const [grabbed, setGrabbed] = useState<Asset | null>(null)
  const [grabError, setGrabError] = useState<string | null>(null)

  useEffect(() => {
    setGrabbed(null)
    setGrabError(null)
    setPageCount(null)
    if (!isPdf) return
    let stale = false
    documentsApi
      .pageCount(assetId)
      .then((count) => {
        if (!stale) setPageCount(count)
      })
      .catch(() => {
        // The grab itself reports a PDF it cannot open; no count just means no ceiling.
      })
    return () => {
      stale = true
    }
  }, [assetId, isPdf])

  async function grab(page: number) {
    setGrabbing(page)
    setGrabError(null)
    try {
      const made = await documentsApi.grabPage(assetId, { page })
      setGrabbed(made)
    } catch (err) {
      setGrabbed(null)
      setGrabError(apiErrorMessage(err, 'Could not grab that page'))
    } finally {
      setGrabbing(null)
    }
  }

  const pageNumber = Number(pageInput)
  const pageValid =
    Number.isInteger(pageNumber) &&
    pageNumber >= 1 &&
    (pageCount === null || pageNumber <= pageCount)

  const load = useCallback(async () => {
    try {
      const [fetched, jobs] = await Promise.all([
        enrichmentApi.text(assetId),
        activityApi.list({ active: true, asset_id: assetId }),
      ])
      setPages(fetched)
      // Only this asset's extraction. `activityApi.list` returns active jobs of every
      // action for the asset, so taking jobs[0] would show a summarise job's progress
      // under this heading — the mistake TranscriptPanel already records.
      setJob(jobs.find((j) => j.action === 'extract_text') ?? null)
      setError(null)
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not load the extracted text'))
    } finally {
      setLoading(false)
    }
  }, [assetId])

  useEffect(() => {
    setLoading(true)
    void load()
  }, [load])

  // The job as the rest of the app sees it. `Tabs` keeps this panel mounted while the
  // "Extract text" button on the Info tab starts a job, so the panel's own fetch above
  // ran long before there was anything to find and nothing here would ever run it again
  // — the document would read "no text yet" beside a job that had succeeded. Reloading on
  // each change of the store's job is what lets two components that cannot see each
  // other agree. The id is part of the key because a re-run goes done -> done.
  const storeJob = useActivityStore((s) =>
    s.jobs.find((j) => j.asset_id === assetId && j.action === 'extract_text')
  )
  const storeJobKey = storeJob ? `${storeJob.id}:${storeJob.status}` : ''
  const seenJobKey = useRef<string | null>(null)
  useEffect(() => {
    // The first run is the mount, which the effect above already loads for.
    if (seenJobKey.current === null) {
      seenJobKey.current = storeJobKey
      return
    }
    if (seenJobKey.current === storeJobKey) return
    seenJobKey.current = storeJobKey
    void load()
  }, [storeJobKey, load])

  // Poll only while something is running. Extracted text is static once written.
  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'processing')) return
    const timer = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(timer)
  }, [job, load])

  const running = job?.status === 'queued' || job?.status === 'processing'

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-gray-200 px-3 py-2 dark:border-gray-700">
        <FileText className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
        <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-200">
          Extracted text
        </h3>
        {pages.length > 0 && (
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {pages.length} {pages.length === 1 ? 'section' : 'sections'}
          </span>
        )}
        {running && (
          <span className="ml-auto flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            {job?.stage || 'Reading…'}
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {/* Here rather than on the Info tab, beside the only place its output is shown.
            The button also reports a failed run's reason — a scan has no text to read —
            so the explanation sits under the empty state that would otherwise invite a
            re-run that cannot work. */}
        <div className="mb-2">
          <EnrichmentButton
            assetId={assetId}
            action="extract_text"
            icon={ScanText}
            label="Extract text"
            runningLabel="Reading…"
            start={enrichmentApi.extractText}
            failureMessage="Could not start reading this document"
            refreshOnFinish={false}
          />
        </div>

        {isPdf && (
          <div className="mb-3 space-y-1 rounded-md border border-gray-200 p-2 dark:border-gray-700">
            <p className="text-xs font-medium text-gray-700 dark:text-gray-200">
              Grab a page as an image
            </p>
            <div className="flex items-center gap-2">
              <label
                className="text-xs text-gray-600 dark:text-gray-400"
                htmlFor="grab-page"
              >
                Page
              </label>
              <input
                id="grab-page"
                type="number"
                min={1}
                max={pageCount ?? undefined}
                value={pageInput}
                onChange={(e) => setPageInput(e.target.value)}
                className="input w-20 px-2 py-1 text-xs"
              />
              {pageCount !== null && (
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  of {pageCount}
                </span>
              )}
              <button
                type="button"
                className="btn btn-secondary px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                disabled={!pageValid || grabbing !== null}
                onClick={() => void grab(pageNumber)}
              >
                {grabbing === pageNumber ? 'Grabbing…' : 'Grab page'}
              </button>
            </div>
            {grabbed && (
              <p className="text-xs text-green-700 dark:text-green-400">
                Saved &ldquo;{grabbed.name}&rdquo; to the library &mdash;{' '}
                <Link to={`/a/${grabbed.id}`} className="underline">
                  view it
                </Link>
              </p>
            )}
            {grabError && (
              <p className="text-xs text-red-600 dark:text-red-400">{grabError}</p>
            )}
          </div>
        )}

        {error && (
          <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
            {error}
          </p>
        )}

        {!error && loading && (
          <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
            Loading…
          </p>
        )}

        {!error && !loading && pages.length === 0 && (
          <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
            {running
              ? 'Reading the document…'
              : 'No text has been read out of this document yet. Run Extract text to make it summarisable.'}
          </p>
        )}

        {!error &&
          pages.map((page) => (
            <div key={page.id} className="mb-3">
              {page.label && (
                <p className="mb-0.5 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                  {page.label}
                  {isPdf && page.page_number != null && (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 normal-case tracking-normal text-blue-700 hover:underline disabled:opacity-50 dark:text-blue-300"
                      disabled={grabbing !== null}
                      onClick={() => void grab(page.page_number as number)}
                    >
                      <ImagePlus className="h-3 w-3" />
                      Grab as image
                    </button>
                  )}
                </p>
              )}
              {/* `whitespace-pre-wrap`: the paragraph breaks extraction preserved are the
                  only structure this text has left. */}
              <p className="whitespace-pre-wrap text-xs leading-relaxed text-gray-700 dark:text-gray-300">
                {page.text}
              </p>
            </div>
          ))}
      </div>
    </div>
  )
}
