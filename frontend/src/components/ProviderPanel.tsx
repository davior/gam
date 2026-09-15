import { useEffect, useState } from 'react'
import { Check, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'
import {
  providersApi,
  type AIProvider,
  type ProviderTestResult,
  type ProviderType,
} from '@/api/providers'
import { apiErrorMessage } from '@/api/client'
import { useSavedFlash } from '@/utils/useSavedFlash'

/**
 * Configuring the LLM that will write descriptions, summaries and tag suggestions.
 *
 * Nothing consumes these rows yet — the jobs that use them are the next step of the
 * milestone — which is exactly why "Test connection" is here: without it there would be
 * no way to find out whether a key and a model name are right.
 */

const TYPES: ProviderType[] = ['anthropic', 'openai', 'deepseek', 'ollama', 'custom']

const TYPE_LABELS: Record<ProviderType, string> = {
  anthropic: 'Anthropic (Claude)',
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  ollama: 'Ollama (runs locally)',
  custom: 'Custom (OpenAI-compatible)',
}

// Per-type output-token defaults. Claude's 4.x models take 64000; most
// OpenAI-compatible models cap output nearer 16384.
const DEFAULT_MAX_TOKENS: Record<ProviderType, number> = {
  anthropic: 64000,
  openai: 16384,
  deepseek: 8192,
  ollama: 16384,
  custom: 16384,
}

// Whether a type's typical model can look at a picture — used to pre-tick the box when
// the user picks a type, not to decide for them. Anthropic and OpenAI are vision-capable
// across their current models; DeepSeek chat is text-only, and local or self-hosted
// models mostly are too, so those start off and the user ticks one on for llava or a
// multimodal gateway.
const DEFAULT_SUPPORTS_IMAGES: Record<ProviderType, boolean> = {
  anthropic: true,
  openai: true,
  deepseek: false,
  ollama: false,
  custom: false,
}

const MODEL_PLACEHOLDERS: Record<ProviderType, string> = {
  anthropic: 'claude-sonnet-4-20250514',
  openai: 'gpt-4o',
  deepseek: 'deepseek-chat',
  ollama: 'llama3.2',
  custom: 'model-name',
}

/**
 * Which types can be pointed at an Anthropic-compatible endpoint instead of their own.
 * DeepSeek publishes one that runs its own server-side web search; a custom gateway may
 * speak Messages too. Anthropic is already there, and Ollama speaks only its own
 * protocol — and its address is allowed to be private, so aiming a Messages request at
 * it would mean aiming it inside the network.
 */
const canUseAnthropicApi = (type: ProviderType) =>
  type === 'deepseek' || type === 'custom'

/** Only these have a base URL worth showing: the others resolve to fixed endpoints. */
const hasBaseUrl = (type: ProviderType) =>
  type === 'openai' || type === 'custom' || type === 'ollama'

interface Form {
  name: string
  provider_type: ProviderType
  api_key: string
  base_url: string
  model: string
  max_tokens: number
  supports_images: boolean
  use_anthropic_api: boolean
  extra_params: string
}

const emptyForm = (): Form => ({
  name: '',
  provider_type: 'anthropic',
  api_key: '',
  base_url: '',
  model: '',
  max_tokens: DEFAULT_MAX_TOKENS.anthropic,
  supports_images: DEFAULT_SUPPORTS_IMAGES.anthropic,
  use_anthropic_api: false,
  extra_params: '',
})

export default function ProviderPanel() {
  const [providers, setProviders] = useState<AIProvider[]>([])
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<Form>(emptyForm())
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null)
  const [saved, flashSaved] = useSavedFlash()
  const [error, setError] = useState<string | null>(null)

  const load = () =>
    providersApi
      .list()
      .then(setProviders)
      .catch((err) => setError(apiErrorMessage(err, 'Could not load providers')))

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function startAdd() {
    setEditingId(null)
    setForm(emptyForm())
    setTestResult(null)
    setError(null)
    setShowForm(true)
  }

  function startEdit(provider: AIProvider) {
    setEditingId(provider.id)
    setForm({
      name: provider.name,
      provider_type: provider.provider_type,
      // Never seeded from the server: it was never sent one. Empty means "leave the
      // stored key alone", which is the same contract the request carries.
      api_key: '',
      base_url: provider.base_url ?? '',
      model: provider.model,
      max_tokens: provider.max_tokens,
      supports_images: provider.supports_images,
      use_anthropic_api: provider.use_anthropic_api,
      extra_params: provider.extra_params
        ? JSON.stringify(provider.extra_params, null, 2)
        : '',
    })
    setTestResult(null)
    setError(null)
    setShowForm(true)
  }

  function changeType(provider_type: ProviderType) {
    setForm((f) => ({
      ...f,
      provider_type,
      max_tokens: DEFAULT_MAX_TOKENS[provider_type],
      supports_images: DEFAULT_SUPPORTS_IMAGES[provider_type],
      use_anthropic_api: canUseAnthropicApi(provider_type) ? f.use_anthropic_api : false,
    }))
    setTestResult(null)
  }

  /** null means the text was not a JSON object, which is not something to send. */
  function parseExtraParams(): Record<string, unknown> | null {
    const raw = form.extra_params.trim()
    if (!raw) return {}
    try {
      const parsed = JSON.parse(raw)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed))
        return null
      return parsed as Record<string, unknown>
    } catch {
      return null
    }
  }

  const save = async () => {
    const extra_params = parseExtraParams()
    if (extra_params === null) {
      setError('Extra parameters must be a JSON object, like {"temperature": 0}')
      return
    }

    setSaving(true)
    setError(null)
    try {
      const payload = {
        name: form.name.trim(),
        provider_type: form.provider_type,
        base_url: hasBaseUrl(form.provider_type) ? form.base_url.trim() || null : null,
        model: form.model.trim(),
        max_tokens: form.max_tokens,
        supports_images: form.supports_images,
        use_anthropic_api:
          canUseAnthropicApi(form.provider_type) && form.use_anthropic_api,
        extra_params,
      }
      if (editingId) {
        // Omitted on edit unless the user typed one, so an unrelated change cannot wipe
        // a stored key. Typing a space and deleting it is how you clear one.
        await providersApi.update(editingId, {
          ...payload,
          ...(form.api_key ? { api_key: form.api_key } : {}),
        })
      } else {
        await providersApi.create({ ...payload, api_key: form.api_key })
      }
      await load()
      setShowForm(false)
      setEditingId(null)
      flashSaved()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save the provider'))
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(
        await providersApi.test({
          ...(editingId ? { provider_id: editingId } : {}),
          provider_type: form.provider_type,
          api_key: form.api_key,
          base_url: hasBaseUrl(form.provider_type) ? form.base_url.trim() || null : null,
          model: form.model.trim(),
          use_anthropic_api:
            canUseAnthropicApi(form.provider_type) && form.use_anthropic_api,
        })
      )
    } catch (err) {
      setTestResult({
        success: false,
        message: apiErrorMessage(err, 'Could not connect'),
      })
    } finally {
      setTesting(false)
    }
  }

  const activate = async (id: string) => {
    setError(null)
    try {
      await providersApi.activate(id)
      await load()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not activate the provider'))
    }
  }

  const remove = async (provider: AIProvider) => {
    if (!confirm(`Delete ${provider.name}?`)) return
    setError(null)
    try {
      await providersApi.remove(provider.id)
      await load()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not delete the provider'))
    }
  }

  const canSave = form.name.trim() !== '' && form.model.trim() !== '' && !saving

  return (
    <section className="card space-y-4 p-5">
      <header className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          AI provider
        </h2>
      </header>

      <p className="text-xs text-gray-600 dark:text-gray-400">
        The model that will write descriptions and summaries, and suggest tags. Keep
        several and switch between them — a local one for bulk work, a stronger one for
        anything that has to look at a picture. Keys are encrypted before they are stored
        and are never sent back to the browser.
      </p>

      {error && (
        <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </p>
      )}

      {providers.length === 0 && !showForm && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          No provider configured yet. Nothing can be described, summarised or tagged
          automatically until there is one.
        </p>
      )}

      {providers.length > 0 && (
        <ul className="space-y-2">
          {providers.map((provider) => (
            <li
              key={provider.id}
              className="flex items-center gap-3 rounded-md border border-gray-200 px-3 py-2 dark:border-gray-800"
            >
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
                  <span className="truncate">{provider.name}</span>
                  {provider.is_active && (
                    <span className="shrink-0 rounded bg-green-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-green-700 dark:bg-green-900/30 dark:text-green-400">
                      Active
                    </span>
                  )}
                </p>
                <p className="truncate text-xs text-gray-500 dark:text-gray-400">
                  {TYPE_LABELS[provider.provider_type]} · {provider.model}
                  {provider.supports_images && ' · can see images'}
                  {provider.use_anthropic_api && ' · Messages protocol'}
                </p>
              </div>

              {!provider.is_active && (
                <button
                  type="button"
                  className="btn btn-ghost text-xs"
                  onClick={() => void activate(provider.id)}
                >
                  Use this one
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost p-1.5"
                aria-label={`Edit ${provider.name}`}
                onClick={() => startEdit(provider)}
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className="btn btn-ghost p-1.5 text-red-600 dark:text-red-400"
                aria-label={`Delete ${provider.name}`}
                onClick={() => void remove(provider)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {!showForm && (
        <button type="button" className="btn btn-secondary" onClick={startAdd}>
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          Add a provider
        </button>
      )}

      {showForm && (
        <div className="space-y-3 border-t border-gray-100 pt-4 dark:border-gray-800">
          <div>
            <label className="label" htmlFor="provider-name">
              Name
            </label>
            <input
              id="provider-name"
              className="input"
              placeholder="Claude"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>

          <div>
            {/* "Provider type", not "Provider": the embedding panel on the same page
                already has a field labelled that, and two identical labels are
                ambiguous to anyone navigating the page by label. */}
            <label className="label" htmlFor="provider-type">
              Provider type
            </label>
            <select
              id="provider-type"
              className="input"
              value={form.provider_type}
              onChange={(e) => changeType(e.target.value as ProviderType)}
            >
              {TYPES.map((type) => (
                <option key={type} value={type}>
                  {TYPE_LABELS[type]}
                </option>
              ))}
            </select>
          </div>

          <div>
            {/* "Model name", not "Model": the settings page shows a transcription model
                and an embedding model too, and three fields labelled the same thing is
                ambiguous to anyone reading the page through a screen reader. */}
            <label className="label" htmlFor="provider-model">
              Model name
            </label>
            <input
              id="provider-model"
              className="input"
              placeholder={MODEL_PLACEHOLDERS[form.provider_type]}
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
            />
            <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
              Typed, not picked from a list — models ship faster than this app is
              redeployed.
            </p>
          </div>

          {form.provider_type !== 'ollama' && (
            <div>
              <label className="label" htmlFor="provider-key">
                Provider API key
              </label>
              <input
                id="provider-key"
                type="password"
                className="input"
                autoComplete="off"
                placeholder={
                  editingId ? 'Leave blank to keep the stored key' : 'Paste your key'
                }
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              />
            </div>
          )}

          {hasBaseUrl(form.provider_type) && (
            <div>
              <label className="label" htmlFor="provider-base-url">
                Base URL
              </label>
              <input
                id="provider-base-url"
                type="url"
                className="input"
                placeholder={
                  form.provider_type === 'ollama'
                    ? 'http://localhost:11434'
                    : 'https://api.openai.com'
                }
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
              <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
                {form.provider_type === 'ollama'
                  ? 'Where Ollama is listening. A local address is expected here.'
                  : 'Optional. Leave blank for the provider’s own endpoint.'}
              </p>
            </div>
          )}

          <div>
            <label className="label" htmlFor="provider-max-tokens">
              Maximum response length
            </label>
            <input
              id="provider-max-tokens"
              type="number"
              className="input"
              min={1}
              max={200000}
              value={form.max_tokens}
              onChange={(e) =>
                setForm({ ...form, max_tokens: Number(e.target.value) || 1 })
              }
            />
          </div>

          <label className="flex items-start gap-2 text-xs text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={form.supports_images}
              onChange={(e) => setForm({ ...form, supports_images: e.target.checked })}
            />
            <span>
              This model can look at images
              <span className="block text-gray-500 dark:text-gray-400">
                Text-only models reject a picture with an unreadable error from upstream,
                so describing images is only offered for providers marked here.
              </span>
            </span>
          </label>

          {canUseAnthropicApi(form.provider_type) && (
            <label className="flex items-start gap-2 text-xs text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={form.use_anthropic_api}
                onChange={(e) =>
                  setForm({ ...form, use_anthropic_api: e.target.checked })
                }
              />
              <span>
                Use the Anthropic-compatible endpoint
                <span className="block text-gray-500 dark:text-gray-400">
                  DeepSeek publishes one that runs the same server-side web search Claude
                  does — no separate search key, no per-search fee. Its ordinary endpoint
                  has no such tool.
                </span>
              </span>
            </label>
          )}

          <div>
            <label className="label" htmlFor="provider-extra-params">
              Extra parameters
            </label>
            <textarea
              id="provider-extra-params"
              className="input font-mono text-xs"
              rows={3}
              placeholder='{"temperature": 0}'
              value={form.extra_params}
              onChange={(e) => setForm({ ...form, extra_params: e.target.value })}
            />
            <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
              A JSON object merged into each request. Leave blank for none.
            </p>
          </div>

          {testResult && (
            <p
              className={
                testResult.success
                  ? 'flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400'
                  : 'text-xs text-red-700 dark:text-red-400'
              }
            >
              {testResult.success && <Check className="h-3.5 w-3.5" />}
              {testResult.message}
            </p>
          )}

          <div className="flex gap-2">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canSave}
              onClick={() => void save()}
            >
              {saving ? 'Saving…' : 'Save provider'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!form.model.trim() || testing}
              onClick={() => void test()}
            >
              {testing ? 'Testing…' : 'Test connection'}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setShowForm(false)
                setEditingId(null)
                setError(null)
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {saved && (
        <p className="flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400">
          <Check className="h-3.5 w-3.5" />
          Saved
        </p>
      )}
    </section>
  )
}
