import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Tags, X } from 'lucide-react'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { enrichmentApi, type Suggestion } from '@/api/enrichment'
import { isActive, useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'

/**
 * What the AI proposed for this asset, and the two buttons that decide it.
 *
 * FR 9.1.4: nothing here is applied until someone presses accept. A rejection is kept
 * rather than discarded, so a later run does not propose the same tag again — being
 * asked twice about something you declined is how a suggestion feature becomes noise.
 */

interface Props {
  assetId: string
}

export default function SuggestionPanel({ assetId }: Props) {
  const job = useActivityStore((s) =>
    s.jobs.find((j) => j.asset_id === assetId && j.action === 'autotag')
  )
  const refreshActivity = useActivityStore((s) => s.refresh)
  const refreshAsset = useLibraryStore((s) => s.refreshAsset)

  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  const running = job ? isActive(job) : false

  const load = useCallback(() => {
    enrichmentApi
      .suggestions(assetId)
      .then(setSuggestions)
      .catch(() => setSuggestions([]))
  }, [assetId])

  useEffect(() => {
    load()
  }, [load])

  // A run produces the suggestions this panel exists to show, so it has to re-read them
  // when one ends. Same running -> finished edge the summarise button uses.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) load()
    wasRunning.current = running
  }, [running, load])

  const start = async () => {
    setError(null)
    setUnavailable(false)
    try {
      await enrichmentApi.autotag(assetId)
      await refreshActivity()
    } catch (err) {
      if (apiErrorCode(err) === 'provider_unavailable') setUnavailable(true)
      else setError(apiErrorMessage(err, 'Could not start tagging'))
    }
  }

  const decide = async (suggestion: Suggestion, accepted: boolean) => {
    setBusy(suggestion.id)
    setError(null)
    try {
      if (accepted) {
        await enrichmentApi.accept(assetId, suggestion.id)
        // Accepting writes to the library, so what is on screen is now stale: a tag
        // attached, or the asset renamed. Re-read the asset rather than guessing which
        // — it comes back with its tags, so one call covers both kinds.
        await refreshAsset(assetId)
      } else {
        await enrichmentApi.reject(assetId, suggestion.id)
      }
      setSuggestions((current) => current.filter((s) => s.id !== suggestion.id))
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save that'))
    } finally {
      setBusy(null)
    }
  }

  const titles = suggestions.filter((s) => s.kind === 'title')
  const tags = suggestions.filter((s) => s.kind === 'tag')

  return (
    <div className="space-y-2">
      <button
        type="button"
        className="btn btn-ghost w-full justify-start text-xs"
        disabled={running}
        onClick={() => void start()}
      >
        <Tags className="mr-1.5 h-3.5 w-3.5" />
        {running ? job?.stage || 'Tagging…' : 'Suggest tags and a title'}
      </button>

      {unavailable && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          No AI provider yet.{' '}
          <Link
            to="/settings"
            className="text-blue-600 hover:underline dark:text-blue-400"
          >
            Add one in Settings
          </Link>
          .
        </p>
      )}
      {!running && job?.status === 'error' && job.error_message && (
        <p className="text-xs text-red-600 dark:text-red-400">{job.error_message}</p>
      )}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}

      {suggestions.length > 0 && (
        <div className="space-y-2 rounded-md border border-blue-200 bg-blue-50/50 p-2.5 dark:border-blue-900 dark:bg-blue-950/20">
          <p className="text-[11px] font-medium uppercase tracking-wide text-blue-800 dark:text-blue-300">
            Suggested — nothing is applied until you say so
          </p>

          {titles.map((suggestion) => (
            <div key={suggestion.id} className="space-y-1">
              <p className="text-[11px] text-gray-500 dark:text-gray-400">Rename to</p>
              <div className="flex items-center gap-1.5">
                <span className="min-w-0 flex-1 truncate text-xs text-gray-900 dark:text-gray-100">
                  {suggestion.value}
                </span>
                <Decide suggestion={suggestion} busy={busy} onDecide={decide} />
              </div>
            </div>
          ))}

          {tags.length > 0 && (
            <div className="space-y-1">
              <p className="text-[11px] text-gray-500 dark:text-gray-400">Tags</p>
              <ul className="flex flex-wrap gap-1.5">
                {tags.map((suggestion) => (
                  <li
                    key={suggestion.id}
                    className="flex items-center gap-1 rounded-full border border-gray-300 bg-white px-2 py-0.5 dark:border-gray-700 dark:bg-gray-900"
                  >
                    <span className="text-xs text-gray-900 dark:text-gray-100">
                      {suggestion.value}
                    </span>
                    <Decide suggestion={suggestion} busy={busy} onDecide={decide} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Decide({
  suggestion,
  busy,
  onDecide,
}: {
  suggestion: Suggestion
  busy: string | null
  onDecide: (s: Suggestion, accepted: boolean) => Promise<void>
}) {
  const disabled = busy === suggestion.id
  return (
    <span className="flex shrink-0 items-center gap-0.5">
      <button
        type="button"
        className="rounded p-0.5 text-green-700 hover:bg-green-100 disabled:opacity-40 dark:text-green-400 dark:hover:bg-green-900/30"
        aria-label={`Accept ${suggestion.value}`}
        disabled={disabled}
        onClick={() => void onDecide(suggestion, true)}
      >
        <Check className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        className="rounded p-0.5 text-gray-500 hover:bg-gray-200 disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-700"
        aria-label={`Dismiss ${suggestion.value}`}
        disabled={disabled}
        onClick={() => void onDecide(suggestion, false)}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </span>
  )
}
