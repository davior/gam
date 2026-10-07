import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Dices, Loader2, Pencil, RefreshCw } from 'lucide-react'
import { assetsApi, type Asset } from '@/api/assets'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import { canBeBase, generateApi, KIND_LABELS, type AssetGeneration } from '@/api/generate'
import { formatCost, type UsageTotals } from '@/api/usage'
import GenerateForm, { type GeneratePreset } from '@/components/GenerateForm'
import { useActivityStore } from '@/stores/activity'
import { useGenerationStore } from '@/stores/generation'
import { formatDate } from '@/utils/format'

interface Props {
  asset: Asset
  /** The asset's spend, already fetched by the Info tab — on a generated asset that is
   *  what the generation cost, and asking for it twice would be two requests for one
   *  number. */
  usage: UsageTotals | null
}

/**
 * An asset's Generate tab: how it was made, and making more.
 *
 * On a generated asset it leads with the provenance — the record is the point of keeping
 * one — and offers to run it again three ways: exactly as before, with fal's reported
 * seed pinned (the nearest thing to the same picture fal offers), or through the full
 * form, pre-filled, to change something first. On an image with a file of its own it
 * also offers that image as a base. A video has no form here: a frame of one cannot be a
 * base yet.
 *
 * Mounted keyed by asset id, so swapping the panel to another asset starts this over
 * rather than carrying a half-open edit across.
 */
export default function GenerateTab({ asset, usage }: Props) {
  const generation = asset.generation
  const catalogue = useGenerationStore((s) => s.models)
  const ensureLoaded = useGenerationStore((s) => s.ensureLoaded)
  const refreshActivity = useActivityStore((s) => s.refresh)

  const [running, setRunning] = useState<'again' | 'seed' | 'edit' | null>(null)
  const [queued, setQueued] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [keyMissing, setKeyMissing] = useState(false)
  const [editing, setEditing] = useState<Asset[] | null>(null)

  useEffect(() => {
    ensureLoaded()
  }, [ensureLoaded])

  const row = generation
    ? catalogue.find((m) => m.endpoint_id === generation.model)
    : undefined
  // Hidden only when the catalogue says the model takes no seed: the server would drop
  // the seed silently, and a button promising the same seed would then be a lie.
  const canReuseSeed =
    generation !== null &&
    generation.seed !== null &&
    row?.options.supports_seed !== false

  const regenerate = async (reuseSeed: boolean) => {
    if (!generation) return
    setRunning(reuseSeed ? 'seed' : 'again')
    setError(null)
    setKeyMissing(false)
    setQueued(null)
    try {
      const job = await generateApi.regenerate(asset.id, {
        prompt: null,
        reuse_seed: reuseSeed,
      })
      setQueued(job.asset_name || generation.prompt)
      void refreshActivity()
    } catch (err) {
      if (apiErrorCode(err) === 'fal_key_missing') setKeyMissing(true)
      else setError(apiErrorMessage(err, 'Could not start regenerating'))
    } finally {
      setRunning(null)
    }
  }

  const edit = async () => {
    if (!generation) return
    setRunning('edit')
    setError(null)
    setQueued(null)
    // The form wants the assets themselves, for their thumbnails — and fetching them is
    // also the check that they still exist. Bases first, then the end frame, which is the
    // order the form reads them in.
    const sources = [
      ...generation.sources.filter((s) => s.role === 'base'),
      ...generation.sources.filter((s) => s.role === 'end_frame'),
    ]
    const found = await Promise.all(
      sources.map((source) =>
        assetsApi.get(source.asset_id).then(
          (fetched) => fetched,
          () => null
        )
      )
    )
    setRunning(null)
    const gone = sources.find((_, index) => found[index] === null)
    if (gone) {
      setError(
        `Could not open “${gone.name}”, one of the images this was made from — it may have been deleted.`
      )
      return
    }
    setEditing(found.filter((a): a is Asset => a !== null))
  }

  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
      {generation && (
        <>
          <Provenance
            generation={generation}
            modelLabel={row?.label ?? null}
            usage={usage}
          />

          <div className="flex flex-wrap gap-2">
            <ActionButton
              icon={<RefreshCw className="h-3.5 w-3.5" />}
              busy={running === 'again'}
              disabled={running !== null}
              onClick={() => void regenerate(false)}
            >
              Regenerate
            </ActionButton>
            {canReuseSeed && (
              <ActionButton
                icon={<Dices className="h-3.5 w-3.5" />}
                busy={running === 'seed'}
                disabled={running !== null}
                onClick={() => void regenerate(true)}
              >
                Regenerate with the same seed
              </ActionButton>
            )}
            <ActionButton
              icon={<Pencil className="h-3.5 w-3.5" />}
              busy={running === 'edit'}
              disabled={running !== null || editing !== null}
              onClick={() => void edit()}
            >
              Edit and generate
            </ActionButton>
          </div>

          {queued && (
            <p role="status" className="text-xs text-green-700 dark:text-green-400">
              Queued &ldquo;{queued}&rdquo; — follow it under background activity.
            </p>
          )}
          {keyMissing && (
            <p role="alert" className="text-xs text-gray-600 dark:text-gray-400">
              Generating needs a fal.ai key.{' '}
              <Link
                to="/settings"
                className="text-blue-700 hover:underline dark:text-blue-400"
              >
                Add one in Settings
              </Link>
              .
            </p>
          )}
          {error && (
            <p role="alert" className="text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
          )}

          {editing && (
            <GenerateForm
              title="Edit and generate"
              bases={editing}
              preset={presetFrom(generation)}
              onCancel={() => setEditing(null)}
            />
          )}
        </>
      )}

      {/* Not while editing: two forms one above the other, both saying Generate, is a
          coin toss about which one runs. */}
      {canBeBase(asset) && !editing && (
        <GenerateForm title="Generate from this image" bases={[asset]} />
      )}
    </div>
  )
}

function presetFrom(generation: AssetGeneration): GeneratePreset {
  return {
    model: generation.model,
    kind: generation.kind,
    prompt: generation.prompt,
    parameters: generation.parameters,
  }
}

function ActionButton({
  icon,
  busy,
  disabled,
  onClick,
  children,
}: {
  icon: ReactNode
  busy: boolean
  disabled: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      className="btn btn-secondary px-3 py-1.5 text-xs"
      disabled={disabled}
      onClick={onClick}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
      {children}
    </button>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1 text-xs">
      <dt className="shrink-0 text-gray-500 dark:text-gray-400">{label}</dt>
      <dd className="min-w-0 break-words text-right text-gray-800 dark:text-gray-200">
        {children}
      </dd>
    </div>
  )
}

/** A parameter as it went to fal — strings bare, anything else as the JSON it was. */
function showValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function Provenance({
  generation,
  modelLabel,
  usage,
}: {
  generation: AssetGeneration
  modelLabel: string | null
  usage: UsageTotals | null
}) {
  const parameters = Object.entries(generation.parameters)

  return (
    <section aria-label="How this was made" className="space-y-3">
      <dl className="divide-y divide-gray-100 dark:divide-gray-800">
        <Fact label="Model">
          {modelLabel ?? generation.model}
          {/* The endpoint id as well as the label: it is what fal bills under, and what
              to search fal's docs for when a result is not what was asked for. */}
          {modelLabel && (
            <span className="block font-mono text-[11px] text-gray-500 dark:text-gray-400">
              {generation.model}
            </span>
          )}
        </Fact>
        <Fact label="Kind">{KIND_LABELS[generation.kind] ?? generation.kind}</Fact>
        {generation.seed !== null && <Fact label="Seed">{generation.seed}</Fact>}
        <Fact label="Generated">{formatDate(generation.generated_at)}</Fact>
        {usage && usage.total_events > 0 && (
          <Fact label="Cost">
            {usage.priced_events > 0
              ? `${formatCost(usage.cost, usage.currency)}${usage.estimated ? ' (est.)' : ''}`
              : 'not priced'}
          </Fact>
        )}
      </dl>

      <div>
        <h3 className="label text-xs">Prompt</h3>
        <p className="whitespace-pre-wrap text-sm text-gray-800 dark:text-gray-200">
          {generation.prompt}
        </p>
      </div>

      {generation.sources.length > 0 && (
        <div>
          <h3 className="label text-xs">Made from</h3>
          <ul className="space-y-0.5 text-xs">
            {generation.sources.map((source) => (
              <li key={`${source.role}-${source.asset_id}`}>
                <Link
                  to={`/a/${source.asset_id}`}
                  className="text-blue-700 hover:underline dark:text-blue-400"
                >
                  {source.name || 'Untitled'}
                </Link>
                {source.role === 'end_frame' && (
                  <span className="text-gray-500 dark:text-gray-400"> · end frame</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {parameters.length > 0 && (
        <div>
          <h3 className="label text-xs">Parameters</h3>
          <dl className="divide-y divide-gray-100 font-mono dark:divide-gray-800">
            {parameters.map(([key, value]) => (
              <Fact key={key} label={key}>
                {showValue(value)}
              </Fact>
            ))}
          </dl>
        </div>
      )}
    </section>
  )
}
