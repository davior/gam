import { useState } from 'react'
import { AudioWaveform, FileSearch, Loader2 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { assetsApi } from '@/api/assets'
import { apiErrorMessage } from '@/api/client'
import type { ActivityJob } from '@/api/transcripts'
import { isActive, useActivityStore } from '@/stores/activity'

/**
 * The two library-wide passes that fill metadata in without asking anything of anyone.
 *
 * Both already happen to every new upload. These buttons are for the files that were in
 * the library before each existed, which on a long-running instance is most of it. Both
 * only ever fill blanks, so pressing either twice, or after hand corrections, is safe;
 * the copy says so, because "will this overwrite what I fixed?" is the question that
 * stops people pressing a library-wide button.
 */

interface ScanSpec {
  action: string
  icon: LucideIcon
  title: string
  body: string
  button: string
  start: () => Promise<ActivityJob>
}

const SCANS: ScanSpec[] = [
  {
    action: 'harvest_attribution',
    icon: FileSearch,
    title: 'Read file tags',
    body:
      'Fills credits from EXIF, ID3 and document properties, and genre, tempo, key and ' +
      'mood from music tags.',
    button: 'Read tags',
    start: () => assetsApi.harvestAttribution(),
  },
  {
    action: 'analyze_music_library',
    icon: AudioWaveform,
    title: 'Analyse music',
    body:
      'Measures tempo, key and energy from the sound of every track still missing one. ' +
      'Files with no beat or key, such as talks, are left alone.',
    button: 'Analyse',
    start: () => assetsApi.analyzeMusicLibrary(),
  },
]

function Scan({ spec }: { spec: ScanSpec }) {
  const job = useActivityStore((s) => s.jobs.find((j) => j.action === spec.action))
  const refreshActivity = useActivityStore((s) => s.refresh)
  const [error, setError] = useState<string | null>(null)
  const running = job ? isActive(job) : false
  const Icon = spec.icon

  const start = async () => {
    setError(null)
    try {
      await spec.start()
      await refreshActivity()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not start'))
    }
  }

  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="flex items-center gap-1.5 text-sm font-medium text-gray-900 dark:text-gray-100">
          <Icon className="h-3.5 w-3.5 text-gray-500" />
          {spec.title}
        </p>
        <p className="mt-0.5 text-xs text-gray-600 dark:text-gray-400">{spec.body}</p>
        {job && !running && job.detail && (
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Last run: {job.detail}
          </p>
        )}
        {error && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{error}</p>}
      </div>
      <button
        type="button"
        className="btn btn-secondary shrink-0"
        onClick={() => void start()}
        disabled={running}
      >
        {running ? (
          <>
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            Running…
          </>
        ) : (
          spec.button
        )}
      </button>
    </div>
  )
}

export default function LibraryScanPanel() {
  return (
    <section className="card space-y-4 p-5">
      <header>
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Fill in metadata
        </h2>
        <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
          New uploads get both of these automatically. Run them once for files that were
          here before. Each fills only empty fields, so nothing you have typed is changed.
        </p>
      </header>
      {SCANS.map((spec) => (
        <Scan key={spec.action} spec={spec} />
      ))}
    </section>
  )
}
