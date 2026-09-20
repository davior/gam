import { useEffect, useMemo, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { Check, Copy, Link2, ScanSearch } from 'lucide-react'
import type { Asset, AssetUpdate, AttributionField } from '@/api/assets'
import { enrichmentApi } from '@/api/enrichment'
import EnrichmentButton from '@/components/EnrichmentButton'
import SuggestionPanel from '@/components/SuggestionPanel'
import { useLibraryStore } from '@/stores/library'
import { useSavedFlash } from '@/utils/useSavedFlash'

/**
 * Where an asset's content came from, so it can be credited when it is used.
 *
 * `Asset.source` says how the file arrived — uploaded, generated, cut from something
 * else. This is the other question: whose work it is. See docs/m10-attribution.md.
 *
 * The values arriving on `asset` are already *resolved*: a clip with nothing of its own
 * carries what it inherited from its parent, and `attribution_inherited` names which.
 * Those render as inherited rather than as ordinary values, because an inherited value
 * that looked typed is one a user would "correct" here — silently detaching that field
 * from the source, so that fixing the parent later no longer reaches it.
 */

interface FieldSpec {
  name: AttributionField
  label: string
  placeholder: string
  hint?: string
  type?: 'text' | 'url' | 'date'
}

const FIELDS: FieldSpec[] = [
  {
    name: 'creator',
    label: 'Creator',
    placeholder: 'Author, photographer, speaker, director',
  },
  {
    name: 'source_title',
    label: 'Source title',
    placeholder: 'The programme, film, article or book this is part of',
  },
  {
    name: 'publisher',
    label: 'Publisher',
    placeholder: 'Outlet, channel, studio, imprint',
  },
  {
    name: 'published_date',
    label: 'Published',
    placeholder: 'YYYY, YYYY-MM or YYYY-MM-DD',
    hint: 'A year alone is fine — better than inventing a day nobody knows.',
  },
  {
    name: 'source_url',
    label: 'Source URL',
    placeholder: 'https://…',
    type: 'url',
  },
  {
    name: 'license',
    label: 'Licence / rights',
    placeholder: 'CC BY 4.0, © the BBC, unknown',
  },
]

/** Accepted by the server: a whole year, a year and month, or a full date. */
const ISO_PARTIAL_DATE = /^\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?$/

type Draft = Record<AttributionField, string>

function draftFrom(asset: Asset): Draft {
  return {
    creator: asset.creator ?? '',
    source_title: asset.source_title ?? '',
    publisher: asset.publisher ?? '',
    published_date: asset.published_date ?? '',
    source_url: asset.source_url ?? '',
    license: asset.license ?? '',
    retrieved_at: asset.retrieved_at ?? '',
    credit_line: asset.credit_line ?? '',
  }
}

/**
 * The same composition the server performs, so the preview updates as you type.
 *
 * Duplicated from `app/attribution.py::compose_credit` on purpose: the alternative is a
 * round trip per keystroke to show a line that is pure formatting. `credit` from the
 * server remains authoritative — this only ever renders an unsaved draft.
 */
function composeCredit(draft: Draft): string {
  return [
    draft.creator,
    draft.source_title,
    draft.publisher,
    draft.published_date,
    draft.license,
  ]
    .map((value) => value.trim())
    .filter(Boolean)
    .join(' — ')
}

interface Props {
  asset: Asset
}

export default function AttributionPanel({ asset }: Props) {
  const update = useLibraryStore((s) => s.update)
  const [draft, setDraft] = useState<Draft>(() => draftFrom(asset))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, flashSaved] = useSavedFlash()
  const [copied, setCopied] = useState(false)

  // Re-seed when the panel is pointed at a different asset, or when the server's copy
  // changes underneath it — a harvest job filling blanks is exactly that case.
  useEffect(() => {
    setDraft(draftFrom(asset))
    setError(null)
    // Deliberately not `[asset]`: the store hands back a new object on every update, so
    // depending on its identity would re-seed the draft mid-edit and discard whatever
    // was half-typed. Re-seeding on a different asset, or on a server-side change to
    // this one (a harvest filling blanks), is the whole intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asset.id, asset.metadata_modified_date])

  const inherited = useMemo(
    () => new Set(asset.attribution_inherited),
    [asset.attribution_inherited]
  )

  const dirty = useMemo(() => {
    const current = draftFrom(asset)
    return (Object.keys(current) as AttributionField[]).some(
      (key) => draft[key] !== current[key]
    )
  }, [asset, draft])

  const dateValid =
    !draft.published_date.trim() || ISO_PARTIAL_DATE.test(draft.published_date.trim())

  // The override when there is one, else the live composition — the same precedence the
  // server applies, so what is previewed is what will be stored.
  const preview = draft.credit_line.trim() || composeCredit(draft)

  const set = (name: AttributionField, value: string) =>
    setDraft((previous) => ({ ...previous, [name]: value }))

  const save = async () => {
    if (!dirty || !dateValid) return
    setSaving(true)
    setError(null)
    try {
      // Only what changed. The API stamps every key it receives as human-written, so
      // sending all eight would mark the seven you did not touch as hand-verified —
      // including the ones a harvest filled, which is the distinction `provenance`
      // exists to keep.
      const current = draftFrom(asset)
      const changes: AssetUpdate = {}
      for (const key of Object.keys(current) as AttributionField[]) {
        if (draft[key] !== current[key]) changes[key] = draft[key].trim() || null
      }

      await update(asset.id, changes)
      flashSaved()
    } catch {
      // The store restores the server's version and surfaces the message; leaving the
      // draft alone means a rejected edit is still on screen to fix.
      setError('Could not save. Your changes are still here.')
    } finally {
      setSaving(false)
    }
  }

  const copyCredit = async () => {
    const line = asset.credit || preview
    if (!line) return
    try {
      await navigator.clipboard.writeText(line)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access needs a secure context and a user gesture. Prompting with the
      // text beats a button that silently does nothing.
      window.prompt('Copy this credit', line)
    }
  }

  // Escape must not reach DetailDock's window listener, which closes the whole panel —
  // abandoning a half-typed credit should not also shut the asset.
  const swallowEscape = (event: KeyboardEvent) => {
    if (event.key === 'Escape') event.stopPropagation()
  }

  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4" onKeyDown={swallowEscape}>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Where this came from, so it can be credited when you use it.
      </p>

      {/* Proposes, never writes — the one enrichment that may not, because a wrong
          citation credits somebody else's work to the wrong outlet in a field that then
          looks finished. Absent on a clip: it has no bytes and no transcript of its own
          to read, and it inherits its original's attribution anyway. */}
      {!asset.parent_asset_id && (
        <EnrichmentButton
          assetId={asset.id}
          action="attribute"
          icon={ScanSearch}
          label="Find the source with AI"
          runningLabel="Looking…"
          start={enrichmentApi.attribute}
          failureMessage="Could not start looking"
        />
      )}

      <SuggestionPanel assetId={asset.id} />

      {FIELDS.map((field) => (
        <div key={field.name}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-2">
            <label className="label" htmlFor={`attribution-${field.name}`}>
              {field.label}
            </label>
            {inherited.has(field.name) && (
              <span className="text-[11px] text-gray-500 dark:text-gray-400">
                inherited from {asset.parent_asset_id ? 'the original' : 'its source'}
              </span>
            )}
          </div>
          <input
            id={`attribution-${field.name}`}
            className={`input ${inherited.has(field.name) ? 'text-gray-500 dark:text-gray-400' : ''}`}
            type={field.type === 'url' ? 'url' : 'text'}
            value={draft[field.name]}
            placeholder={field.placeholder}
            aria-invalid={field.name === 'published_date' && !dateValid}
            onChange={(e) => set(field.name, e.target.value)}
          />
          {field.name === 'published_date' && !dateValid && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">
              Use YYYY, YYYY-MM or YYYY-MM-DD.
            </p>
          )}
          {field.hint && dateValid && field.name === 'published_date' && (
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{field.hint}</p>
          )}
        </div>
      ))}

      <div>
        <label className="label" htmlFor="attribution-credit_line">
          Credit line
        </label>
        <input
          id="attribution-credit_line"
          className="input"
          value={draft.credit_line}
          placeholder={composeCredit(draft) || 'Composed from the fields above'}
          onChange={(e) => set('credit_line', e.target.value)}
        />
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Leave this empty and it is built from the fields above, so correcting one keeps
          the credit right.
        </p>
      </div>

      {preview && (
        <div className="rounded-md border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800/50">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="label mb-1">Credit</p>
              <p className="break-words text-sm text-gray-900 dark:text-gray-100">
                {preview}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void copyCredit()}
              className="btn btn-ghost shrink-0 p-1.5"
              title="Copy this credit"
              aria-label="Copy this credit"
            >
              {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            </button>
          </div>
        </div>
      )}

      {asset.source_url && (
        <a
          href={asset.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1.5 text-sm text-blue-600 hover:underline dark:text-blue-400"
        >
          <Link2 className="h-3.5 w-3.5" />
          Open the source
        </a>
      )}

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void save()}
          disabled={!dirty || saving || !dateValid}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-sm text-green-600 dark:text-green-400">
            <Check className="h-4 w-4" />
            Saved
          </span>
        )}
      </div>
    </div>
  )
}
