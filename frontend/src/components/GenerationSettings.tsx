import { useEffect, useId, useState, type ReactNode } from 'react'
import { Check, Pencil, Plus, Trash2, WandSparkles } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import {
  formatListPrice,
  GENERATION_KINDS,
  generateApi,
  KIND_LABELS,
  type GenerationKind,
  type GenerationModel,
  type GenerationModelInput,
  type OptionValue,
} from '@/api/generate'
import { generationSettingsApi, type GenerationSettings } from '@/api/settings'
import { useAuthStore } from '@/stores/auth'
import { useGenerationStore } from '@/stores/generation'
import { useSavedFlash } from '@/utils/useSavedFlash'

/**
 * Settings for M8: the user's fal.ai key, and the model catalogue everyone shares.
 *
 * The key follows the Deepgram panel exactly — written, never read back. The catalogue is
 * the other half and a different kind of thing: one global list of fal endpoints, kept as
 * data because fal renames and retires endpoint ids every few months. Everybody sees it,
 * since it is what the generate forms offer; only an admin can change it, and the server
 * checks that again on every write — hiding the buttons is a courtesy, not the guard.
 */
export default function GenerationSettingsPanel() {
  const isAdmin = useAuthStore((s) => s.user?.is_admin ?? false)

  return (
    <section className="card space-y-4 p-5">
      <header className="flex items-center gap-2">
        <WandSparkles className="h-4 w-4 text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Image and video generation
        </h2>
      </header>

      <FalKey />
      <Catalogue isAdmin={isAdmin} />
    </section>
  )
}

function FalKey() {
  const [settings, setSettings] = useState<GenerationSettings | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, flashSaved] = useSavedFlash()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    generationSettingsApi
      .get()
      .then(setSettings)
      .catch((err) => setError(apiErrorMessage(err, 'Could not load the fal.ai key')))
  }, [])

  const save = async (fal_api_key: string) => {
    setSaving(true)
    setError(null)
    try {
      setSettings(await generationSettingsApi.update({ fal_api_key }))
      setKeyInput('')
      flashSaved()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-600 dark:text-gray-400">
        Generating images and video uses{' '}
        <a
          href="https://fal.ai/dashboard/keys"
          target="_blank"
          rel="noreferrer"
          className="text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
        >
          fal.ai
        </a>
        , billed to your own account. Your key is encrypted before it is stored and is
        never sent back to the browser.
      </p>

      <ErrorBox message={error} />

      <div>
        <label className="label" htmlFor="fal-key">
          fal.ai API key
        </label>
        <div className="flex gap-2">
          <input
            id="fal-key"
            type="password"
            className="input flex-1"
            placeholder={
              settings?.fal_key_configured ? 'A key is configured' : 'Paste your key'
            }
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            autoComplete="off"
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={!keyInput.trim() || saving}
            onClick={() => void save(keyInput)}
            aria-label="Save fal.ai key"
          >
            Save
          </button>
        </div>
        {settings?.fal_key_configured && (
          <button
            type="button"
            className="mt-1.5 text-xs text-red-600 hover:underline dark:text-red-400"
            // Named apart from the Deepgram and OpenAI buttons that read the same on
            // screen: a screen reader listing the page's buttons would otherwise offer
            // three identical "Remove the stored key"s.
            aria-label="Remove the stored fal.ai key"
            onClick={() => void save('')}
          >
            Remove the stored key
          </button>
        )}
      </div>

      {saved && (
        <p className="flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400">
          <Check className="h-3.5 w-3.5" />
          Saved
        </p>
      )}
    </div>
  )
}

/** The editable shape: every field as the control holds it, converted on save. */
interface ModelForm {
  endpoint_id: string
  kind: GenerationKind
  label: string
  note: string
  sort_order: string
  is_active: boolean
  image_field: string
  image_field_is_list: boolean
  max_images: string
  end_image_field: string
  aspect_ratios: string
  image_sizes: string
  durations: string
  resolutions: string
  supports_seed: boolean
  supports_negative_prompt: boolean
  supports_audio: boolean
  max_outputs: string
  extra_params: string
  unit_price: string
  price_unit: string
  price_currency: string
}

const PRICE_UNITS = ['image', 'megapixel', 'second', 'video']

const emptyForm = (): ModelForm => ({
  endpoint_id: '',
  kind: 'text_to_image',
  label: '',
  note: '',
  sort_order: '0',
  is_active: true,
  image_field: '',
  image_field_is_list: false,
  max_images: '1',
  end_image_field: '',
  aspect_ratios: '',
  image_sizes: '',
  durations: '',
  resolutions: '',
  supports_seed: false,
  supports_negative_prompt: false,
  supports_audio: false,
  max_outputs: '1',
  extra_params: '',
  unit_price: '',
  price_unit: '',
  price_currency: 'USD',
})

const NUMERIC = /^-?\d+(\.\d+)?$/

/**
 * A list of endpoint values as one line of text, and back.
 *
 * The values keep their type through the round trip, because the endpoints do not agree
 * on one: Kling's durations are `"5"`, Hailuo's are `6`. A bare number is a number; a
 * quoted one is text; anything else (`8s`, `16:9`) is text without needing the quotes.
 * So `"5"` shows quoted, `6` bare, and saving either back changes nothing.
 */
function formatList(values: OptionValue[]): string {
  return values
    .map((v) => (typeof v === 'number' ? String(v) : NUMERIC.test(v) ? `"${v}"` : v))
    .join(', ')
}

function parseList(text: string): OptionValue[] {
  return text
    .split(',')
    .map((token) => token.trim())
    .filter(Boolean)
    .map((token) => {
      const quoted = /^"(.*)"$/.exec(token)
      if (quoted) return quoted[1]
      return NUMERIC.test(token) ? Number(token) : token
    })
}

function fromModel(model: GenerationModel): ModelForm {
  return {
    endpoint_id: model.endpoint_id,
    kind: model.kind,
    label: model.label,
    note: model.note,
    sort_order: String(model.sort_order),
    is_active: model.is_active,
    image_field: model.image_field ?? '',
    image_field_is_list: model.image_field_is_list,
    max_images: String(model.max_images),
    end_image_field: model.end_image_field ?? '',
    aspect_ratios: formatList(model.options.aspect_ratios),
    image_sizes: formatList(model.options.image_sizes),
    durations: formatList(model.options.durations),
    resolutions: formatList(model.options.resolutions),
    supports_seed: model.options.supports_seed,
    supports_negative_prompt: model.options.supports_negative_prompt,
    supports_audio: model.options.supports_audio,
    max_outputs: String(model.options.max_outputs),
    extra_params:
      Object.keys(model.extra_params).length > 0
        ? JSON.stringify(model.extra_params, null, 2)
        : '',
    unit_price: model.unit_price === null ? '' : String(model.unit_price),
    price_unit: model.price_unit ?? '',
    price_currency: model.price_currency ?? '',
  }
}

function wholeNumber(text: string): number | null {
  return /^\d+$/.test(text.trim()) ? Number(text.trim()) : null
}

/**
 * The form as a request body, or the sentence saying why it is not one.
 *
 * Mirrors the server's `invalid_model_entry` rules so they are caught before a round
 * trip, and so the message can name the field; the server still has the last word. The
 * fields a kind cannot use are sent as null or zero rather than left as typed — every
 * field goes in the PATCH, so switching an image model to text → image clears its image
 * field rather than leaving one the server would refuse.
 */
function toInput(form: ModelForm): GenerationModelInput | string {
  const endpoint_id = form.endpoint_id.trim()
  if (!endpoint_id) return 'An endpoint id is required, like fal-ai/flux/dev'
  const label = form.label.trim()
  if (!label) return 'A label is required'

  const sort_order = wholeNumber(form.sort_order)
  if (sort_order === null) return 'Sort order must be a whole number'

  const takesImages = form.kind !== 'text_to_image'
  const video = form.kind === 'image_to_video'
  const image_field = form.image_field.trim()
  const max_images = takesImages ? wholeNumber(form.max_images) : 0
  if (takesImages && !image_field) {
    return 'An image model needs the field its endpoint reads the image from, like image_url'
  }
  if (max_images === null || (takesImages && max_images < 1)) {
    return 'Maximum base images must be at least 1'
  }

  const max_outputs = wholeNumber(form.max_outputs)
  if (video && max_outputs !== 1) return 'A video model makes one output at a time'
  if (max_outputs === null || max_outputs < 1 || max_outputs > 4) {
    return 'Maximum outputs must be between 1 and 4'
  }

  let extra_params: Record<string, unknown> = {}
  if (form.extra_params.trim()) {
    try {
      const parsed: unknown = JSON.parse(form.extra_params)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        throw new Error('not an object')
      }
      extra_params = parsed as Record<string, unknown>
    } catch {
      return 'Extra parameters must be a JSON object, like {"generate_audio": false}'
    }
  }

  const priceText = form.unit_price.trim()
  const unit_price = priceText ? Number(priceText) : null
  if (unit_price !== null && (!Number.isFinite(unit_price) || unit_price < 0)) {
    return 'The list price must be a number'
  }

  return {
    endpoint_id,
    kind: form.kind,
    label,
    note: form.note.trim(),
    sort_order,
    is_active: form.is_active,
    image_field: takesImages ? image_field : null,
    image_field_is_list: takesImages && form.image_field_is_list,
    max_images,
    end_image_field:
      video && form.end_image_field.trim() ? form.end_image_field.trim() : null,
    options: {
      aspect_ratios: parseList(form.aspect_ratios),
      image_sizes: parseList(form.image_sizes),
      durations: parseList(form.durations),
      resolutions: parseList(form.resolutions),
      supports_seed: form.supports_seed,
      supports_negative_prompt: form.supports_negative_prompt,
      supports_audio: form.supports_audio,
      max_outputs,
    },
    extra_params,
    unit_price,
    price_unit: form.price_unit || null,
    price_currency: form.price_currency.trim() || null,
  }
}

function inCatalogueOrder(a: GenerationModel, b: GenerationModel): number {
  return a.sort_order - b.sort_order || a.label.localeCompare(b.label)
}

function Catalogue({ isAdmin }: { isAdmin: boolean }) {
  const reloadForms = useGenerationStore((s) => s.load)
  const [models, setModels] = useState<GenerationModel[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState<ModelForm | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, flashSaved] = useSavedFlash()

  // An admin sees the parked rows too; for anyone else the server would ignore the flag.
  useEffect(() => {
    let current = true
    generateApi
      .models(isAdmin)
      .then((rows) => {
        if (current) setModels(rows)
      })
      .catch((err) => {
        if (current) setError(apiErrorMessage(err, 'Could not load the models'))
      })
    return () => {
      current = false
    }
  }, [isAdmin])

  /** Fold a saved row in locally, and tell the generate forms their list changed. */
  const changed = (row: GenerationModel | null, removedId?: string) => {
    setModels((current) => {
      const rest = (current ?? []).filter((m) => m.id !== (row?.id ?? removedId))
      return row ? [...rest, row] : rest
    })
    void reloadForms()
  }

  const startAdd = () => {
    setEditingId(null)
    setForm(emptyForm())
    setError(null)
  }

  const startEdit = (model: GenerationModel) => {
    setEditingId(model.id)
    setForm(fromModel(model))
    setError(null)
  }

  const close = () => {
    setForm(null)
    setEditingId(null)
    setError(null)
  }

  const save = async () => {
    if (!form) return
    const input = toInput(form)
    if (typeof input === 'string') {
      setError(input)
      return
    }
    setSaving(true)
    setError(null)
    try {
      changed(
        editingId
          ? await generateApi.updateModel(editingId, input)
          : await generateApi.createModel(input)
      )
      setForm(null)
      setEditingId(null)
      flashSaved()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save the model'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (model: GenerationModel) => {
    setError(null)
    try {
      changed(await generateApi.updateModel(model.id, { is_active: !model.is_active }))
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not change the model'))
    }
  }

  const remove = async (model: GenerationModel) => {
    // Deactivating is the reversible way to stop offering one; deleting loses the row's
    // dialect, which someone looked up in fal's schema to write.
    if (!confirm(`Delete ${model.label}? Deactivating it keeps it for later.`)) return
    setError(null)
    try {
      await generateApi.deleteModel(model.id)
      changed(null, model.id)
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not delete the model'))
    }
  }

  return (
    <div className="space-y-3 border-t border-gray-100 pt-4 dark:border-gray-800">
      <h3 className="text-xs font-semibold text-gray-900 dark:text-gray-100">Models</h3>
      <p className="text-xs text-gray-600 dark:text-gray-400">
        {isAdmin
          ? 'The fal.ai endpoints the generate forms offer, for everyone. fal retires and renames endpoints, so this list is kept here rather than in the app.'
          : 'The fal.ai endpoints the generate forms offer. An administrator keeps this list.'}
      </p>

      <ErrorBox message={error} />

      {models && models.length === 0 && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          No models yet — nothing can be generated until one is added.
        </p>
      )}

      {models &&
        GENERATION_KINDS.map((kind) => {
          const rows = models.filter((m) => m.kind === kind).sort(inCatalogueOrder)
          if (rows.length === 0) return null
          return (
            <div key={kind} className="space-y-1.5">
              <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400">
                {KIND_LABELS[kind]}
              </h4>
              <ul className="space-y-1.5">
                {rows.map((model) => (
                  <ModelRow
                    key={model.id}
                    model={model}
                    actions={
                      isAdmin && (
                        <>
                          <button
                            type="button"
                            className="btn btn-ghost px-2 py-1 text-xs"
                            onClick={() => void toggle(model)}
                            aria-label={`${model.is_active ? 'Deactivate' : 'Activate'} ${model.label}`}
                          >
                            {model.is_active ? 'Deactivate' : 'Activate'}
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost p-1.5"
                            aria-label={`Edit ${model.label}`}
                            onClick={() => startEdit(model)}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost p-1.5 text-red-600 dark:text-red-400"
                            aria-label={`Delete ${model.label}`}
                            onClick={() => void remove(model)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </>
                      )
                    }
                  />
                ))}
              </ul>
            </div>
          )
        })}

      {isAdmin && !form && (
        <button type="button" className="btn btn-secondary" onClick={startAdd}>
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          Add a model
        </button>
      )}

      {isAdmin && form && (
        <ModelEditor
          form={form}
          onChange={setForm}
          editing={editingId !== null}
          saving={saving}
          onSave={() => void save()}
          onCancel={close}
        />
      )}

      {saved && (
        <p className="flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400">
          <Check className="h-3.5 w-3.5" />
          Saved
        </p>
      )}
    </div>
  )
}

function ModelRow({ model, actions }: { model: GenerationModel; actions: ReactNode }) {
  const price = formatListPrice(model)
  return (
    <li className="flex items-center gap-3 rounded-md border border-gray-200 px-3 py-2 dark:border-gray-800">
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
          <span className="truncate">{model.label}</span>
          {!model.is_active && (
            <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:bg-gray-700 dark:text-gray-300">
              Inactive
            </span>
          )}
        </p>
        <p className="truncate font-mono text-[11px] text-gray-500 dark:text-gray-400">
          {model.endpoint_id}
        </p>
        {(model.note || price) && (
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {[model.note, price].filter(Boolean).join(' · ')}
          </p>
        )}
      </div>
      {actions}
    </li>
  )
}

function ModelEditor({
  form,
  onChange,
  editing,
  saving,
  onSave,
  onCancel,
}: {
  form: ModelForm
  onChange: (form: ModelForm) => void
  editing: boolean
  saving: boolean
  onSave: () => void
  onCancel: () => void
}) {
  const id = useId()
  const set = <K extends keyof ModelForm>(key: K, value: ModelForm[K]) =>
    onChange({ ...form, [key]: value })
  const takesImages = form.kind !== 'text_to_image'
  const video = form.kind === 'image_to_video'

  const text = (key: keyof ModelForm, label: string, placeholder = '', hint?: string) => (
    <div>
      <label className="label text-xs" htmlFor={`${id}-${key}`}>
        {label}
      </label>
      <input
        id={`${id}-${key}`}
        className="input"
        placeholder={placeholder}
        value={form[key] as string}
        onChange={(e) => set(key, e.target.value)}
      />
      {hint && <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
    </div>
  )

  const check = (key: keyof ModelForm, label: string) => (
    <label className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300">
      <input
        type="checkbox"
        checked={form[key] as boolean}
        onChange={(e) => set(key, e.target.checked)}
      />
      {label}
    </label>
  )

  return (
    <div
      className="space-y-3 border-t border-gray-100 pt-4 dark:border-gray-800"
      role="group"
      aria-label={editing ? 'Edit model' : 'New model'}
    >
      {text('endpoint_id', 'Endpoint id', 'fal-ai/flux/dev')}

      <div>
        <label className="label text-xs" htmlFor={`${id}-kind`}>
          Kind
        </label>
        <select
          id={`${id}-kind`}
          className="input"
          value={form.kind}
          onChange={(e) => {
            const kind = e.target.value as GenerationKind
            // A video endpoint makes one output; carrying an image model's 4 across
            // would only produce the validation message on save.
            onChange({
              ...form,
              kind,
              max_outputs: kind === 'image_to_video' ? '1' : form.max_outputs,
            })
          }}
        >
          {GENERATION_KINDS.map((kind) => (
            <option key={kind} value={kind}>
              {KIND_LABELS[kind]}
            </option>
          ))}
        </select>
      </div>

      {text('label', 'Label', 'FLUX.1 [dev]')}
      {text(
        'note',
        'Note',
        '',
        'Shown under the model in the form — what it is good at.'
      )}
      {text('sort_order', 'Sort order')}
      {check('is_active', 'Active — offered in the generate forms')}

      {takesImages && (
        <>
          {text(
            'image_field',
            'Image field',
            'image_url',
            'The request field the base image goes in, as fal’s schema names it.'
          )}
          {check('image_field_is_list', 'The image field takes a list')}
          {text('max_images', 'Maximum base images')}
        </>
      )}
      {video &&
        text(
          'end_image_field',
          'End-frame field',
          'tail_image_url',
          'Leave blank for an endpoint that takes no last frame.'
        )}

      {text(
        'aspect_ratios',
        'Aspect ratios',
        '16:9, 9:16, 1:1',
        'Each list is comma-separated, exactly as the endpoint takes it. A bare number goes as a number; quote it ("5") for an endpoint that wants text. Leave a list blank to not offer that choice.'
      )}
      {text('image_sizes', 'Image sizes', 'square_hd, landscape_16_9')}
      {text('durations', 'Durations', '"5", "10"')}
      {text('resolutions', 'Resolutions', '720p, 1080p')}
      {check('supports_seed', 'Takes a seed')}
      {check('supports_negative_prompt', 'Takes a negative prompt')}
      {check('supports_audio', 'Can generate audio')}
      {text(
        'max_outputs',
        'Maximum outputs',
        '',
        video ? 'Always 1 for video.' : '1 to 4.'
      )}

      <div>
        <label className="label text-xs" htmlFor={`${id}-extra`}>
          Extra parameters
        </label>
        <textarea
          id={`${id}-extra`}
          className="input font-mono text-xs"
          rows={3}
          placeholder='{"generate_audio": false}'
          value={form.extra_params}
          onChange={(e) => set('extra_params', e.target.value)}
        />
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          A JSON object merged into every request first — a default the form does not
          expose. Leave blank for none.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {text('unit_price', 'List price', '0.025')}
        <div>
          <label className="label text-xs" htmlFor={`${id}-price_unit`}>
            Price unit
          </label>
          <select
            id={`${id}-price_unit`}
            className="input"
            value={form.price_unit}
            onChange={(e) => set('price_unit', e.target.value)}
          >
            <option value="">None</option>
            {PRICE_UNITS.map((unit) => (
              <option key={unit} value={unit}>
                {unit}
              </option>
            ))}
          </select>
        </div>
        {text('price_currency', 'Currency', 'USD')}
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Only the fallback: a generation is costed from fal’s own pricing when it can be
        asked, and from this when it cannot.
      </p>

      <div className="flex gap-2">
        <button
          type="button"
          className="btn btn-primary"
          disabled={saving}
          onClick={onSave}
        >
          {saving ? 'Saving…' : 'Save model'}
        </button>
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
      {message}
    </p>
  )
}
