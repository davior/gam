import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, CheckCheck, X } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import { enrichmentApi, type Suggestion } from '@/api/enrichment'
import { isActive, useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'

/**
 * What the AI proposed for this asset, and the controls that decide it.
 *
 * FR 9.1.4: nothing here is applied until someone presses accept. A rejection is kept
 * rather than discarded, so a later run does not propose the same tag again — being
 * asked twice about something you declined is how a suggestion feature becomes noise.
 *
 * Starting the run itself is the icon button beside the Tags label (see AssetDetail) —
 * this panel only reads what came back and applies or drops it, watching the activity
 * store for the same job to know when to reload rather than owning its own trigger.
 */

interface Props {
  assetId: string
}

/** Field names as the Source tab labels them, so the two do not disagree. */
const FIELD_LABELS: Record<string, string> = {
  creator: 'Creator',
  publisher: 'Publisher',
  source_title: 'Source title',
  published_date: 'Published',
  source_url: 'Source URL',
  license: 'Licence / rights',
}

export default function SuggestionPanel({ assetId }: Props) {
  // Autotag can also be run through "Generate all", which is a different action on
  // the same asset — this still has to notice that run ending, or accepted-looking
  // suggestions from it would sit unread until something else reloaded the panel.
  const job = useActivityStore((s) =>
    s.jobs.find(
      (j) =>
        j.asset_id === assetId &&
        (j.action === 'autotag' ||
          j.action === 'generate_all' ||
          j.action === 'attribute')
    )
  )
  const refreshAsset = useLibraryStore((s) => s.refreshAsset)

  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [acceptingAll, setAcceptingAll] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  // Sequential, not Promise.all: each accept writes to the same asset (a tag attach,
  // or a rename), and `decide` already re-reads it afterwards — running them at once
  // would have those reads racing each other for no real speed benefit on what is at
  // most a handful of suggestions.
  const acceptAll = async () => {
    setAcceptingAll(true)
    try {
      for (const suggestion of suggestions) {
        await decide(suggestion, true)
      }
    } finally {
      setAcceptingAll(false)
    }
  }

  const titles = suggestions.filter((s) => s.kind === 'title')
  const tags = suggestions.filter((s) => s.kind === 'tag')
  // Skips any row the server could not decode — a malformed payload should cost its own
  // suggestion, not the panel listing every other one beside it.
  const attributions = suggestions.filter(
    (s) => s.kind === 'attribution' && s.field && s.proposed_value
  )

  if (suggestions.length === 0) {
    return error ? (
      <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
    ) : null
  }

  return (
    <div className="space-y-2 rounded-md border border-blue-200 bg-blue-50/50 p-2.5 dark:border-blue-900 dark:bg-blue-950/20">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-wide text-blue-800 dark:text-blue-300">
          Suggested — nothing is applied until you say so
        </p>
        {suggestions.length > 1 && (
          <button
            type="button"
            className="flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[11px] font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40 dark:text-blue-300 dark:hover:bg-blue-900/40"
            disabled={acceptingAll || busy !== null}
            onClick={() => void acceptAll()}
          >
            <CheckCheck className="h-3 w-3" />
            Accept all
          </button>
        )}
      </div>

      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}

      {titles.map((suggestion) => (
        <div key={suggestion.id} className="space-y-1">
          <p className="text-[11px] text-gray-500 dark:text-gray-400">Rename to</p>
          <div className="flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-xs text-gray-900 dark:text-gray-100">
              {suggestion.value}
            </span>
            <Decide
              suggestion={suggestion}
              disabled={busy === suggestion.id || acceptingAll}
              onDecide={decide}
            />
          </div>
        </div>
      ))}

      {attributions.map((suggestion) => (
        <div key={suggestion.id} className="space-y-1">
          <p className="text-[11px] text-gray-500 dark:text-gray-400">
            {FIELD_LABELS[suggestion.field ?? ''] ?? suggestion.field}
          </p>
          <div className="flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-xs text-gray-900 dark:text-gray-100">
              {suggestion.proposed_value}
            </span>
            <Decide
              suggestion={suggestion}
              disabled={busy === suggestion.id || acceptingAll}
              onDecide={decide}
            />
          </div>
          {/* Shown rather than tucked away: attribution is the one enrichment that may
              never write directly, and the evidence is what makes accepting a judgement
              rather than a reflex. */}
          {suggestion.evidence && (
            <p className="text-[11px] italic text-gray-500 dark:text-gray-400">
              “{suggestion.evidence}”
            </p>
          )}
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
                <Decide
                  suggestion={suggestion}
                  disabled={busy === suggestion.id || acceptingAll}
                  onDecide={decide}
                />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Decide({
  suggestion,
  disabled,
  onDecide,
}: {
  suggestion: Suggestion
  disabled: boolean
  onDecide: (s: Suggestion, accepted: boolean) => Promise<void>
}) {
  // An attribution row's `value` is the JSON payload the server encoded, so naming the
  // button after it would read out a blob to a screen reader — and to anyone hovering.
  const label = suggestion.proposed_value ?? suggestion.value

  return (
    <span className="flex shrink-0 items-center gap-0.5">
      <button
        type="button"
        className="rounded p-0.5 text-green-700 hover:bg-green-100 disabled:opacity-40 dark:text-green-400 dark:hover:bg-green-900/30"
        aria-label={`Accept ${label}`}
        disabled={disabled}
        onClick={() => void onDecide(suggestion, true)}
      >
        <Check className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        className="rounded p-0.5 text-gray-500 hover:bg-gray-200 disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-700"
        aria-label={`Dismiss ${label}`}
        disabled={disabled}
        onClick={() => void onDecide(suggestion, false)}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </span>
  )
}
