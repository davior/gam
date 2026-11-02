import { ExternalLink } from 'lucide-react'

interface Props {
  suiteUrl: string
}

/**
 * Stands in for the AI provider panel and the embedding settings when enrichment and
 * search run on Gecko Suite (AI_BACKEND=suite). Both would be wrong there rather than
 * merely redundant: a provider or embedding key saved here is never used.
 */
export default function SuiteNotice({ suiteUrl }: Props) {
  const base = suiteUrl.replace(/\/$/, '')
  return (
    <section className="card space-y-3 p-5">
      <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
        AI is managed in Gecko Suite
      </h2>
      <p className="text-sm text-gray-600 dark:text-gray-400">
        Describe, Summarize and Autotag use the models chosen in Gecko Suite, and so does
        semantic search. What they spend is counted there along with Notes and Video
        Creator. Search still works without it, by keyword.
      </p>
      <div className="flex flex-wrap gap-2">
        {[
          { path: '/providers', label: 'Providers' },
          { path: '/routing', label: 'Choose models' },
          { path: '/usage', label: 'Usage' },
        ].map(({ path, label }) => (
          <a
            key={path}
            href={`${base}${path}`}
            target="_blank"
            rel="noreferrer noopener"
            className="btn btn-secondary px-3 py-1"
          >
            {label}
            <ExternalLink className="h-3 w-3" />
          </a>
        ))}
      </div>
    </section>
  )
}
