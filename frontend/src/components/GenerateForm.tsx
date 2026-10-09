import { useEffect, useId, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Loader2, Sparkles } from 'lucide-react'
import type { Asset } from '@/api/assets'
import { apiErrorCode, apiErrorMessage } from '@/api/client'
import {
  formatListPrice,
  generateApi,
  KIND_LABELS,
  type GenerationCreate,
  type GenerationKind,
  type GenerationModel,
  type GenerationOptions,
  type OptionValue,
} from '@/api/generate'
import AssetThumb from '@/components/AssetThumb'
import { useActivityStore } from '@/stores/activity'
import { useGenerationStore } from '@/stores/generation'

/**
 * One form for every way into fal.ai — the add panel, a selection, an asset's own tab.
 *
 * What it can make follows from what it was handed. No bases is text → image. One or two
 * is image → image or image → video, where a second base can only be the video's end
 * frame; three or more is image → image alone. The model list is then the catalogue's
 * rows of that kind that can actually take these bases, and every option control below
 * it renders only when the chosen row declares it — the server refuses anything else, so
 * offering it would only be offering a 422.
 *
 * Like `UrlImport`, it owns no progress of its own: submitting queues a job, says so, and
 * leaves the rest to the activity indicator and to `LibraryView`, which puts the results
 * in the grid when the job finishes.
 */

/** "Edit and generate": a stored generation, to start the form from. */
export interface GeneratePreset {
  /** The endpoint id, which is what a generated asset records — catalogue rows come
   *  and go, so it is matched by endpoint rather than by row id. */
  model: string
  kind: GenerationKind
  prompt: string
  /** The request body as it was sent. Only the keys this form has controls for are
   *  read back; the rest are catalogue defaults that will be merged in again anyway. */
  parameters: Record<string, unknown>
}

interface Props {
  /** Says what this form is for, on screen and as its accessible name — the library
   *  page can have three of these mounted at once. */
  title: string
  /** The base images, in order. The order matters: for a video the first is the start
   *  frame and the second the end frame. */
  bases?: Asset[]
  preset?: GeneratePreset
  onCancel?: () => void
}

type ChoiceKey = 'aspect_ratio' | 'image_size' | 'duration' | 'resolution'

/** The four "pick one of the values the endpoint accepts" controls. */
const CHOICES: Array<{
  key: ChoiceKey
  list: keyof Pick<
    GenerationOptions,
    'aspect_ratios' | 'image_sizes' | 'durations' | 'resolutions'
  >
  label: string
}> = [
  { key: 'aspect_ratio', list: 'aspect_ratios', label: 'Aspect ratio' },
  { key: 'image_size', list: 'image_sizes', label: 'Image size' },
  { key: 'duration', list: 'durations', label: 'Duration' },
  { key: 'resolution', list: 'resolutions', label: 'Resolution' },
]

const NO_CHOICES: Record<ChoiceKey, string> = {
  aspect_ratio: '',
  image_size: '',
  duration: '',
  resolution: '',
}

/** What the server accepts for this many bases — see the component's own comment. */
function kindsFor(baseCount: number): GenerationKind[] {
  if (baseCount === 0) return ['text_to_image']
  if (baseCount <= 2) return ['image_to_image', 'image_to_video']
  return ['image_to_image']
}

/** Whether a row of this kind can take these bases, so the server will not refuse it. */
function fits(model: GenerationModel, kind: GenerationKind, baseCount: number): boolean {
  if (model.kind !== kind) return false
  if (kind === 'image_to_image') return model.max_images >= baseCount
  if (kind === 'image_to_video') return baseCount === 1 || model.end_image_field !== null
  return true
}

function roleOf(kind: GenerationKind, index: number, count: number): string {
  if (kind === 'image_to_video') return index === 0 ? 'Start frame' : 'End frame'
  return count === 1 ? 'Base image' : `Base ${index + 1}`
}

/** The option controls hold strings; the endpoint's own value is looked up on submit,
 *  so a `6` goes out as a number and a `"5"` as a string, exactly as declared. */
function declared(values: OptionValue[], chosen: string): OptionValue | null {
  return values.find((value) => String(value) === chosen) ?? null
}

function presetChoices(preset?: GeneratePreset): Record<ChoiceKey, string> {
  if (!preset) return NO_CHOICES
  const read = (key: ChoiceKey) => {
    const value = preset.parameters[key]
    return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
  }
  return {
    aspect_ratio: read('aspect_ratio'),
    image_size: read('image_size'),
    duration: read('duration'),
    resolution: read('resolution'),
  }
}

function presetOutputs(preset?: GeneratePreset): number {
  // `num_images` is fal's name for it, and so what the stored request body carries.
  const value = preset?.parameters.num_images
  return typeof value === 'number' && value >= 1 ? value : 1
}

export default function GenerateForm({ title, bases = [], preset, onCancel }: Props) {
  const id = useId()
  const catalogue = useGenerationStore((s) => s.models)
  const catalogueLoaded = useGenerationStore((s) => s.loaded)
  const catalogueError = useGenerationStore((s) => s.error)
  const ensureLoaded = useGenerationStore((s) => s.ensureLoaded)
  const refreshActivity = useActivityStore((s) => s.refresh)

  const [kindChoice, setKindChoice] = useState<GenerationKind | null>(
    preset?.kind ?? null
  )
  const [modelId, setModelId] = useState<string | null>(null)
  const [prompt, setPrompt] = useState(preset?.prompt ?? '')
  const [choices, setChoices] = useState(() => presetChoices(preset))
  const [seed, setSeed] = useState(() => {
    const value = preset?.parameters.seed
    return typeof value === 'number' ? String(value) : ''
  })
  const [negative, setNegative] = useState(() => {
    const value = preset?.parameters.negative_prompt
    return typeof value === 'string' ? value : ''
  })
  // Null until touched, and shown as the row's own default until then. What is sent is
  // always what the box shows: sending null instead would leave it to the endpoint,
  // whose default is audio *on* for Veo and Kling v3 — at half again the price — while
  // the box said off for any row an admin added without pinning `generate_audio`.
  const [audio, setAudio] = useState<boolean | null>(() => {
    const value = preset?.parameters.generate_audio
    return typeof value === 'boolean' ? value : null
  })
  const [outputs, setOutputs] = useState(() => presetOutputs(preset))

  const [submitting, setSubmitting] = useState(false)
  const [queued, setQueued] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [keyMissing, setKeyMissing] = useState(false)

  useEffect(() => {
    ensureLoaded()
  }, [ensureLoaded])

  const kinds = kindsFor(bases.length)
  const kind = kindChoice && kinds.includes(kindChoice) ? kindChoice : kinds[0]

  const ofKind = useMemo(
    () => catalogue.filter((m) => m.kind === kind),
    [catalogue, kind]
  )
  const candidates = useMemo(
    () =>
      ofKind
        .filter((m) => fits(m, kind, bases.length))
        .sort((a, b) => a.sort_order - b.sort_order || a.label.localeCompare(b.label)),
    [ofKind, kind, bases.length]
  )

  // Derived rather than stored, so a catalogue that arrives after the form mounts, or a
  // kind switch that empties the old choice, still lands on something offered.
  const model =
    candidates.find((m) => m.id === modelId) ??
    candidates.find((m) => m.endpoint_id === preset?.model) ??
    candidates[0] ??
    null
  const presetModelGone =
    preset !== undefined &&
    catalogueLoaded &&
    !catalogue.some((m) => m.endpoint_id === preset.model)

  const options = model?.options
  const maxOutputs = Math.max(1, options?.max_outputs ?? 1)
  const price = model ? formatListPrice(model) : null

  const unavailable = (): string => {
    if (catalogueError) return catalogueError
    if (!catalogueLoaded) return 'Loading models…'
    if (kind === 'image_to_video' && bases.length > 1 && ofKind.length > 0) {
      return 'None of the video models takes an end frame. Select one image to use as the start frame.'
    }
    if (kind === 'image_to_image' && ofKind.length > 0) {
      return `No image → image model takes ${bases.length} base images.`
    }
    return `No ${KIND_LABELS[kind].toLowerCase()} model is available yet.`
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const text = prompt.trim()
    if (!text || !model || submitting) return
    if (options?.supports_seed && seed.trim() && !/^\d+$/.test(seed.trim())) {
      setError('The seed has to be a whole number')
      return
    }

    const video = kind === 'image_to_video'
    const request: GenerationCreate = {
      model_id: model.id,
      prompt: text,
      base_asset_ids:
        kind === 'text_to_image'
          ? []
          : video
            ? bases.slice(0, 1).map((b) => b.id)
            : bases.map((b) => b.id),
      end_frame_asset_id: video && bases[1] ? bases[1].id : null,
      params: {
        aspect_ratio: declared(model.options.aspect_ratios, choices.aspect_ratio),
        image_size: declared(model.options.image_sizes, choices.image_size),
        duration: declared(model.options.durations, choices.duration),
        resolution: declared(model.options.resolutions, choices.resolution),
        seed: model.options.supports_seed && seed.trim() ? Number(seed.trim()) : null,
        negative_prompt:
          model.options.supports_negative_prompt && negative.trim()
            ? negative.trim()
            : null,
        generate_audio: model.options.supports_audio
          ? (audio ?? model.extra_params.generate_audio === true)
          : null,
        num_outputs: Math.min(Math.max(1, outputs), maxOutputs),
      },
    }

    setSubmitting(true)
    setError(null)
    setKeyMissing(false)
    setQueued(null)
    try {
      const job = await generateApi.start(request)
      // The prompt stays: the next thing anyone does after a generation is adjust the
      // wording and go again, and retyping it would be the whole cost of that.
      setQueued(job.asset_name || text)
      void refreshActivity()
    } catch (err) {
      // A signpost rather than a failure, as a missing AI provider is elsewhere.
      if (apiErrorCode(err) === 'fal_key_missing') setKeyMissing(true)
      else setError(apiErrorMessage(err, 'Could not start generating'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={(e) => void submit(e)}
      className="space-y-3 rounded-lg border border-gray-200 px-3 py-2.5 dark:border-gray-700"
      aria-label={title}
    >
      <p className="flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-300">
        <Sparkles className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
        {title}
      </p>

      {bases.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Base images">
          {bases.map((base, index) => (
            <li key={base.id} className="w-20 space-y-0.5">
              <AssetThumb asset={base} className="h-20 w-20 rounded" />
              <p className="text-[11px] font-medium text-gray-700 dark:text-gray-300">
                {roleOf(kind, index, bases.length)}
              </p>
              <p
                className="truncate text-[11px] text-gray-500 dark:text-gray-400"
                title={base.name}
              >
                {base.name}
              </p>
            </li>
          ))}
        </ul>
      )}

      {kinds.length > 1 && (
        <fieldset className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-700 dark:text-gray-300">
          <legend className="sr-only">What to make</legend>
          {kinds.map((each) => (
            <label key={each} className="flex items-center gap-1.5">
              <input
                type="radio"
                name={`${id}-kind`}
                checked={kind === each}
                onChange={() => setKindChoice(each)}
              />
              {KIND_LABELS[each]}
            </label>
          ))}
        </fieldset>
      )}

      <div>
        <label className="label text-xs" htmlFor={`${id}-model`}>
          Model
        </label>
        {model ? (
          <select
            id={`${id}-model`}
            className="input"
            value={model.id}
            onChange={(e) => setModelId(e.target.value)}
          >
            {candidates.map((each) => (
              <option key={each.id} value={each.id}>
                {each.label}
              </option>
            ))}
          </select>
        ) : (
          <p className="text-xs text-gray-500 dark:text-gray-400">{unavailable()}</p>
        )}
        {model && (model.note || price) && (
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {[model.note, price].filter(Boolean).join(' · ')}
          </p>
        )}
        {presetModelGone && (
          <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
            {preset?.model}, which made this, is no longer offered — pick another.
          </p>
        )}
      </div>

      <div>
        <label className="label text-xs" htmlFor={`${id}-prompt`}>
          Prompt
        </label>
        <textarea
          id={`${id}-prompt`}
          className="input resize-y"
          rows={3}
          maxLength={4000}
          placeholder={
            kind === 'text_to_image'
              ? 'Describe the image you want'
              : kind === 'image_to_video'
                ? 'Describe what happens'
                : 'Describe the change'
          }
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
      </div>

      {options && (
        <div className="grid grid-cols-1 gap-3 @md:grid-cols-2">
          {CHOICES.map(({ key, list, label }) => {
            const values = options[list]
            if (values.length === 0) return null
            const chosen = values.some((v) => String(v) === choices[key])
              ? choices[key]
              : ''
            return (
              <div key={key}>
                <label className="label text-xs" htmlFor={`${id}-${key}`}>
                  {label}
                </label>
                <select
                  id={`${id}-${key}`}
                  className="input"
                  value={chosen}
                  onChange={(e) => setChoices({ ...choices, [key]: e.target.value })}
                >
                  <option value="">Model default</option>
                  {values.map((value) => (
                    <option key={String(value)} value={String(value)}>
                      {String(value)}
                    </option>
                  ))}
                </select>
              </div>
            )
          })}

          {maxOutputs > 1 && (
            <div>
              <label className="label text-xs" htmlFor={`${id}-outputs`}>
                Number of outputs
              </label>
              <select
                id={`${id}-outputs`}
                className="input"
                value={Math.min(outputs, maxOutputs)}
                onChange={(e) => setOutputs(Number(e.target.value))}
              >
                {Array.from({ length: maxOutputs }, (_, i) => i + 1).map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>
          )}

          {options.supports_seed && (
            <div>
              <label className="label text-xs" htmlFor={`${id}-seed`}>
                Seed
              </label>
              <input
                id={`${id}-seed`}
                className="input"
                type="text"
                inputMode="numeric"
                placeholder="Random"
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
              />
            </div>
          )}
        </div>
      )}

      {options?.supports_negative_prompt && (
        <div>
          <label className="label text-xs" htmlFor={`${id}-negative`}>
            Negative prompt
          </label>
          <input
            id={`${id}-negative`}
            className="input"
            placeholder="What to keep out of it"
            value={negative}
            onChange={(e) => setNegative(e.target.value)}
          />
        </div>
      )}

      {options?.supports_audio && model && (
        <label className="flex items-center gap-1.5 text-xs text-gray-700 dark:text-gray-300">
          <input
            type="checkbox"
            checked={audio ?? model.extra_params.generate_audio === true}
            onChange={(e) => setAudio(e.target.checked)}
          />
          Generate audio
        </label>
      )}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          className="btn btn-primary px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!prompt.trim() || !model || submitting}
        >
          {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Generate'}
        </button>
        {onCancel && (
          <button
            type="button"
            className="btn btn-ghost px-3 py-1.5 text-xs"
            onClick={onCancel}
          >
            Cancel
          </button>
        )}
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
    </form>
  )
}
