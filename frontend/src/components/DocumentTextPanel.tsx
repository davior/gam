import { useCallback, useEffect, useState } from 'react'
import { FileText, Loader2 } from 'lucide-react'
import { enrichmentApi, type DocumentPage } from '@/api/enrichment'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { apiErrorMessage } from '@/api/client'

interface Props {
  assetId: string
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
export default function DocumentTextPanel({ assetId }: Props) {
  const [pages, setPages] = useState<DocumentPage[]>([])
  const [job, setJob] = useState<ActivityJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

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
                <p className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                  {page.label}
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
