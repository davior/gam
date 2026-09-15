import { useEffect, useState } from 'react'
import { Receipt } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import { formatCost, usageApi, type UsageSummary } from '@/api/usage'

/**
 * What enrichment has cost so far.
 *
 * Every figure is qualified. `docs/m6-ai-enrichment.md` is explicit that a cost derived
 * from the pricing table is a published list price rather than a bill, and that GAM must
 * not show one without saying so — the tests that used to assert this panel showed *no*
 * price were protecting exactly that, back when there was no pricing table to be honest
 * about.
 */

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  ollama: 'Ollama',
  custom: 'Custom endpoint',
  deepgram: 'Deepgram',
  unknown: 'Unattributed',
}

function formatMinutes(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  return `${minutes.toLocaleString()} minute${minutes === 1 ? '' : 's'}`
}

export default function UsagePanel() {
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    usageApi
      .summary()
      .then(setSummary)
      .catch((err) => setError(apiErrorMessage(err, 'Could not load usage')))
  }, [])

  const totals = summary?.totals
  const unpriced = totals ? totals.total_events - totals.priced_events : 0

  return (
    <section className="card space-y-4 p-5">
      <header className="flex items-center gap-2">
        <Receipt className="h-4 w-4 text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          What enrichment has cost
        </h2>
      </header>

      {error && (
        <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </p>
      )}

      {totals && totals.total_events === 0 && (
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Nothing yet. Describing, summarising and tagging are what spend money here.
        </p>
      )}

      {totals && totals.total_events > 0 && (
        <>
          <div>
            <p className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
              {formatCost(totals.cost, totals.currency)}
            </p>
            <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
              An estimate from published list prices, not a bill. Providers change prices
              and offer discounts this does not model — check your provider dashboard for
              what you were actually charged.
            </p>
          </div>

          <dl className="space-y-1 border-t border-gray-100 pt-3 text-xs dark:border-gray-800">
            <div className="flex justify-between">
              <dt className="text-gray-500 dark:text-gray-400">Tokens</dt>
              <dd className="text-gray-900 dark:text-gray-100">
                {totals.tokens.toLocaleString()}
              </dd>
            </div>
            {totals.seconds > 0 && (
              <div className="flex justify-between">
                <dt className="text-gray-500 dark:text-gray-400">Transcribed</dt>
                <dd className="text-gray-900 dark:text-gray-100">
                  {formatMinutes(totals.seconds)}
                </dd>
              </div>
            )}
            {summary.by_provider.map((row) => (
              <div key={row.provider} className="flex justify-between">
                <dt className="text-gray-500 dark:text-gray-400">
                  {PROVIDER_LABELS[row.provider] ?? row.provider}
                </dt>
                <dd className="text-gray-900 dark:text-gray-100">
                  {row.cost === null
                    ? `${row.events} call${row.events === 1 ? '' : 's'}, not priced`
                    : formatCost(row.cost, totals.currency)}
                </dd>
              </div>
            ))}
          </dl>

          {unpriced > 0 && (
            // Said out loud rather than folded into the total: a figure that silently
            // omitted these would read as complete.
            <p className="text-xs text-amber-700 dark:text-amber-400">
              {unpriced} of {totals.total_events} calls could not be priced — a custom
              endpoint, a model this app does not have a rate for, or transcription. The
              total above excludes them.
            </p>
          )}
        </>
      )}
    </section>
  )
}
