import { useEffect, useRef, useState } from 'react'
import { Activity, X } from 'lucide-react'
import type { ActivityJob } from '@/api/transcripts'
import { isActive, useActivityStore } from '@/stores/activity'

/**
 * What is happening right now, anywhere.
 *
 * A backfill belongs to no asset, so it has nowhere else to appear — and a transcription
 * used to become invisible the moment its asset modal was closed.
 */

const LABELS: Record<string, string> = {
  transcribe: 'Transcribing',
  embed: 'Embedding',
  backfill_embeddings: 'Embedding the library',
  bulk_enrich: 'Enriching a selection',
  describe: 'Describing',
  summarize: 'Summarizing',
  autotag: 'Suggesting tags',
  extract_text: 'Reading text',
}

function subject(job: ActivityJob): string {
  // A whole-library job carries no asset name, and "" would render as a blank line.
  return job.asset_name || 'Your whole library'
}

export default function ActivityIndicator() {
  const jobs = useActivityStore((s) => s.jobs)
  const start = useActivityStore((s) => s.start)
  const cancel = useActivityStore((s) => s.cancel)
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    start()
  }, [start])

  useEffect(() => {
    if (!open) return

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    const onClick = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }

    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  const running = jobs.filter(isActive)

  // Nothing has ever run: no icon at all, rather than a permanently grey one.
  if (jobs.length === 0) return null

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        className="btn btn-ghost relative p-2"
        aria-label={
          running.length > 0
            ? `${running.length} job${running.length === 1 ? '' : 's'} running`
            : 'Background activity'
        }
        onClick={() => setOpen((o) => !o)}
      >
        <Activity
          className={
            running.length > 0
              ? 'h-4 w-4 animate-pulse text-blue-500'
              : 'h-4 w-4 text-gray-400'
          }
        />
        {running.length > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-500 px-1 text-[10px] font-semibold text-white">
            {running.length}
          </span>
        )}
      </button>

      {open && (
        <div className="card absolute right-0 z-50 mt-1 w-80 space-y-2 p-3 text-left shadow-lg">
          <h2 className="text-xs font-semibold text-gray-900 dark:text-gray-100">
            Background activity
          </h2>

          {jobs.map((job) => (
            <div
              key={job.id}
              className="space-y-1 border-t border-gray-100 pt-2 dark:border-gray-800"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-gray-900 dark:text-gray-100">
                    {LABELS[job.action] ?? job.action}
                  </p>
                  <p className="truncate text-xs text-gray-500 dark:text-gray-400">
                    {subject(job)}
                  </p>
                </div>
                {isActive(job) && (
                  <button
                    type="button"
                    className="btn btn-ghost p-1"
                    aria-label={`Cancel ${LABELS[job.action] ?? job.action}`}
                    onClick={() => void cancel(job)}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>

              <p className="text-xs text-gray-500 dark:text-gray-400">
                {job.stalled
                  ? 'Not responding'
                  : job.status === 'error'
                    ? (job.error_message ?? 'Failed')
                    : job.detail || job.stage || job.status}
                {isActive(job) && job.progress > 0 && ` · ${job.progress}%`}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
