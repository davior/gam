import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Loader2, Mic, Pencil, X } from 'lucide-react'
import {
  activityApi,
  transcriptsApi,
  type ActivityJob,
  type Transcript,
  type TranscriptSegment,
} from '@/api/transcripts'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { formatDuration } from '@/utils/format'

interface Props {
  assetId: string
  /** Seconds to jump the player to. The panel does not own the player. */
  onSeek: (seconds: number) => void
  /** Where the player currently is, so the spoken segment can be highlighted. */
  currentTime: number
}

/** How often to poll a running job. Fast enough to feel live, slow enough not to
 *  hammer the API for a job that takes minutes. */
const POLL_MS = 2000

export default function TranscriptPanel({ assetId, onSeek, currentTime }: Props) {
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [job, setJob] = useState<ActivityJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [fetched, jobs] = await Promise.all([
        transcriptsApi.get(assetId),
        activityApi.list({ active: true, asset_id: assetId }),
      ])
      setTranscript(fetched)
      // Only this asset's *transcription*. `activityApi.list` returns active jobs of
      // every action for the asset, newest first, and transcription now queues an embed
      // job the moment it succeeds — so taking jobs[0] showed the embed job's progress
      // under the transcription heading and pointed Cancel at the wrong job.
      setJob(jobs.find((j) => j.action === 'transcribe') ?? null)
      setError(null)
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not load the transcript'))
    } finally {
      setLoading(false)
    }
  }, [assetId])

  useEffect(() => {
    setLoading(true)
    void load()
  }, [load])

  // Poll only while something is running. A finished transcript is static, and
  // polling it forever would be a request every two seconds for nothing.
  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'processing')) return

    const timer = setInterval(() => {
      void load()
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [job, load])

  const start = async () => {
    setError(null)
    try {
      setJob(await transcriptsApi.start(assetId))
    } catch (err) {
      // The one failure worth its own wording: nothing is wrong with the file, the
      // user just has not added a key yet.
      if (apiErrorCode(err) === 'not_transcribable') {
        setError('Only audio and video can be transcribed.')
      } else {
        setError(apiErrorMessage(err, 'Could not start transcription'))
      }
    }
  }

  const cancel = async () => {
    if (!job) return
    try {
      setJob(await activityApi.cancel(job.kind, job.id))
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not cancel'))
    }
  }

  const saveSegment = async (segment: TranscriptSegment, text: string) => {
    const trimmed = text.trim()
    setEditingId(null)
    if (!trimmed || trimmed === segment.text) return

    try {
      const saved = await transcriptsApi.updateSegment(assetId, segment.id, {
        text: trimmed,
      })
      setTranscript((current) =>
        current
          ? {
              ...current,
              segments: current.segments.map((s) => (s.id === saved.id ? saved : s)),
            }
          : current
      )
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save that correction'))
    }
  }

  if (loading) {
    return (
      <p className="p-4 text-xs text-gray-500 dark:text-gray-400">Loading transcript…</p>
    )
  }

  const running = job && (job.status === 'queued' || job.status === 'processing')
  const segments = transcript?.segments ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-gray-200 px-3 py-2 dark:border-gray-700">
        <h3 className="text-xs font-semibold text-gray-900 dark:text-gray-100">
          Transcript
        </h3>

        <div className="ml-auto flex items-center gap-2">
          {running ? (
            <>
              <span className="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400">
                <Loader2 className="h-3 w-3 animate-spin" />
                {job.stalled ? 'Not responding' : job.stage || 'Working'}
                {job.progress > 0 && ` ${job.progress}%`}
              </span>
              <button
                type="button"
                onClick={cancel}
                className="btn btn-ghost px-2 py-1 text-xs"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={start}
              className="btn btn-secondary px-2 py-1 text-xs"
            >
              <Mic className="mr-1 h-3 w-3" />
              {segments.length ? 'Re-transcribe' : 'Transcribe'}
            </button>
          )}
        </div>
      </div>

      {job?.status === 'error' && job.error_message && (
        <p className="border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
          {job.error_message}
        </p>
      )}

      {error && (
        <p className="border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {error}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        {segments.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-gray-500 dark:text-gray-400">
            {running
              ? 'Working on it…'
              : 'No transcript yet. Transcribing makes every spoken word searchable, with a timestamp you can jump to.'}
          </p>
        ) : (
          <ol className="divide-y divide-gray-100 dark:divide-gray-800">
            {segments.map((segment) => (
              <SegmentRow
                key={segment.id}
                segment={segment}
                active={
                  currentTime >= segment.start_time && currentTime < segment.end_time
                }
                editing={editingId === segment.id}
                onEdit={() => setEditingId(segment.id)}
                onCancelEdit={() => setEditingId(null)}
                onSave={(text) => saveSegment(segment, text)}
                onSeek={onSeek}
              />
            ))}
          </ol>
        )}
      </div>

      {transcript?.model && segments.length > 0 && (
        <p className="border-t border-gray-200 px-3 py-1.5 text-[11px] text-gray-500 dark:border-gray-700 dark:text-gray-500">
          {transcript.model}
          {transcript.language && ` · ${transcript.language}`} · {segments.length}{' '}
          segments
        </p>
      )}
    </div>
  )
}

interface RowProps {
  segment: TranscriptSegment
  active: boolean
  editing: boolean
  onEdit: () => void
  onCancelEdit: () => void
  onSave: (text: string) => void
  onSeek: (seconds: number) => void
}

function SegmentRow({
  segment,
  active,
  editing,
  onEdit,
  onCancelEdit,
  onSave,
  onSeek,
}: RowProps) {
  const [draft, setDraft] = useState(segment.text)
  const rowRef = useRef<HTMLLIElement>(null)

  useEffect(() => {
    setDraft(segment.text)
  }, [segment.text])

  // Follow playback. `nearest` rather than `center` so the list only moves when the
  // spoken line would otherwise scroll out of view — constant recentring is worse to
  // read than a list that mostly sits still.
  useEffect(() => {
    if (active && !editing) {
      rowRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [active, editing])

  if (editing) {
    return (
      <li className="bg-blue-50/50 px-3 py-2 dark:bg-blue-950/20">
        <textarea
          className="input min-h-[60px] w-full resize-y text-xs"
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter saves, Shift+Enter makes a newline — a correction is usually one
            // line and reaching for the mouse for each is tedious.
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSave(draft)
            }
            if (e.key === 'Escape') onCancelEdit()
          }}
        />
        <div className="mt-1 flex gap-1">
          <button
            type="button"
            onClick={() => onSave(draft)}
            className="btn btn-primary px-2 py-1 text-xs"
          >
            <Check className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={onCancelEdit}
            className="btn btn-ghost px-2 py-1 text-xs"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      </li>
    )
  }

  return (
    <li
      ref={rowRef}
      className={`group flex gap-2 px-3 py-1.5 ${
        active ? 'bg-blue-50 dark:bg-blue-950/30' : ''
      }`}
    >
      <button
        type="button"
        onClick={() => onSeek(segment.start_time)}
        className="shrink-0 font-mono text-[11px] tabular-nums text-blue-600 hover:underline dark:text-blue-400"
        title="Jump to this moment"
      >
        {formatDuration(segment.start_time)}
      </button>

      <p className="min-w-0 flex-1 break-words text-xs text-gray-800 dark:text-gray-200">
        {segment.speaker !== null && (
          <span className="mr-1 font-medium text-gray-500 dark:text-gray-400">
            S{segment.speaker + 1}:
          </span>
        )}
        {segment.text}
        {segment.edited && (
          <span
            className="ml-1 text-[10px] text-gray-400"
            title="Corrected by hand — a re-run will not overwrite this"
          >
            (edited)
          </span>
        )}
      </p>

      <button
        type="button"
        onClick={onEdit}
        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
        aria-label="Edit this segment"
      >
        <Pencil className="h-3 w-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" />
      </button>
    </li>
  )
}
