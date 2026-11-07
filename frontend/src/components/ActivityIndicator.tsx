import { useEffect, useRef, useState } from 'react'
import { Activity, Check, Copy, X } from 'lucide-react'
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
  import_url: 'Importing',
  make_version: 'Making a version',
  import_track: 'Importing a track',
  generate: 'Generating',
  harvest_attribution: 'Reading file metadata',
  analyze_music: 'Analysing music',
  analyze_music_library: 'Analysing your music',
}

function subject(job: ActivityJob): string {
  // A whole-library job carries no asset name, and "" would render as a blank line.
  return job.asset_name || 'Your whole library'
}

/**
 * A job's error, with any command in it set apart to be copied.
 *
 * An import from a site that blocks the server ends with the yt-dlp command that fetches
 * the same video at home (ytdlp.local_download_command). It is only useful whole and
 * exact, which a line of 12px prose in a narrow card is not, so a line that starts
 * `yt-dlp ` renders as code with a copy button. Plain text in, so the message stays a
 * sentence anywhere else it is shown.
 */
function JobError({ message }: { message: string }) {
  const lines = message.split('\n')
  const command = lines.find((line) => line.startsWith('yt-dlp '))
  const prose = lines.filter((line) => line !== command).join(' ')
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    if (!command) return
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access needs a secure context. Prompting with the text beats a button
      // that silently does nothing.
      window.prompt('Copy this command', command)
    }
  }

  return (
    <>
      <span>{prose}</span>
      {command && (
        <span className="mt-1 flex items-start gap-1">
          <code className="block min-w-0 flex-1 select-all break-all rounded bg-gray-100 p-1.5 font-mono text-[11px] text-gray-800 dark:bg-gray-800 dark:text-gray-200">
            {command}
          </code>
          <button
            type="button"
            className="btn btn-ghost shrink-0 p-1"
            aria-label="Copy this command"
            onClick={() => void copy()}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>
        </span>
      )}
    </>
  )
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

              <div className="text-xs text-gray-500 dark:text-gray-400">
                {job.stalled ? (
                  'Not responding'
                ) : job.status === 'error' ? (
                  <JobError message={job.error_message ?? 'Failed'} />
                ) : (
                  job.detail || job.stage || job.status
                )}
                {isActive(job) && job.progress > 0 && ` · ${job.progress}%`}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
