import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Check, KeyRound, Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  embeddingSettingsApi,
  speechSettingsApi,
  type EmbeddingSettings,
  type SpeechSettings,
} from '@/api/settings'
import { apiErrorMessage } from '@/api/client'
import { embeddingsApi, type EmbeddingCoverage } from '@/api/embeddings'
import { isActive, useActivityStore } from '@/stores/activity'

/** A saved-tick that clears itself, shared by both panels. */
function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [
    saved,
    () => {
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    },
  ]
}

function SpeechPanel() {
  const [settings, setSettings] = useState<SpeechSettings | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, flashSaved] = useSavedFlash()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    speechSettingsApi
      .get()
      .then(setSettings)
      .catch((err) => setError(apiErrorMessage(err, 'Could not load settings')))
  }, [])

  const save = async (changes: {
    deepgram_api_key?: string
    deepgram_model?: string
  }) => {
    setSaving(true)
    setError(null)
    try {
      setSettings(await speechSettingsApi.update(changes))
      setKeyInput('')
      flashSaved()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="card space-y-4 p-5">
      <header className="flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Speech to text
        </h2>
      </header>

      <p className="text-xs text-gray-600 dark:text-gray-400">
        Transcription uses{' '}
        <a
          href="https://console.deepgram.com/"
          target="_blank"
          rel="noreferrer"
          className="text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
        >
          Deepgram
        </a>
        . Your key is encrypted before it is stored and is never sent back to the browser
        — this app makes the calls on your behalf.
      </p>

      {error && (
        <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </p>
      )}

      <div>
        <label className="label" htmlFor="deepgram-key">
          API key
        </label>
        <div className="flex gap-2">
          <input
            id="deepgram-key"
            type="password"
            className="input flex-1"
            placeholder={
              settings?.deepgram_key_configured ? 'A key is configured' : 'Paste your key'
            }
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            autoComplete="off"
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={!keyInput.trim() || saving}
            onClick={() => save({ deepgram_api_key: keyInput })}
            aria-label="Save Deepgram key"
          >
            Save
          </button>
        </div>
        {settings?.deepgram_key_configured && (
          <button
            type="button"
            className="mt-1.5 text-xs text-red-600 hover:underline dark:text-red-400"
            onClick={() => save({ deepgram_api_key: '' })}
          >
            Remove the stored key
          </button>
        )}
      </div>

      {settings && (
        <div>
          <label className="label" htmlFor="deepgram-model">
            Model
          </label>
          <select
            id="deepgram-model"
            className="input"
            value={settings.deepgram_model}
            onChange={(e) => save({ deepgram_model: e.target.value })}
          >
            {settings.available_models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
              </option>
            ))}
          </select>
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

/**
 * Semantic search configuration.
 *
 * Until this panel existed there was no way to turn semantic search on at all: the
 * settings keys were defined and read by the embedder, and nothing could write them.
 * The search view has always told people to "add an embedding provider in Settings" —
 * this is the screen it means.
 */
function EmbeddingPanel() {
  const [settings, setSettings] = useState<EmbeddingSettings | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [urlInput, setUrlInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, flashSaved] = useSavedFlash()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    embeddingSettingsApi
      .get()
      .then((next) => {
        setSettings(next)
        setUrlInput(next.ollama_base_url)
      })
      .catch((err) => setError(apiErrorMessage(err, 'Could not load settings')))
  }, [])

  const save = async (changes: Parameters<typeof embeddingSettingsApi.update>[0]) => {
    setSaving(true)
    setError(null)
    try {
      const next = await embeddingSettingsApi.update(changes)
      setSettings(next)
      setUrlInput(next.ollama_base_url)
      setKeyInput('')
      flashSaved()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save'))
    } finally {
      setSaving(false)
    }
  }

  const isOpenAI = settings?.provider === 'openai'

  // Coverage is model-relative, so it is reloaded whenever the provider or model
  // changes — which is what makes the "changing the model leaves existing vectors
  // behind" warning below actionable rather than ominous: the pending count jumps and
  // the button comes back.
  const [coverage, setCoverage] = useState<EmbeddingCoverage | null>(null)
  const configured = settings?.configured ?? false
  const activeModel = settings?.model

  const loadCoverage = useCallback(() => {
    if (!configured) {
      setCoverage(null)
      return
    }
    embeddingsApi
      .status()
      .then(setCoverage)
      .catch(() => setCoverage(null))
  }, [configured])

  useEffect(() => {
    loadCoverage()
  }, [loadCoverage, activeModel])

  const backfillJob = useActivityStore((s) =>
    s.jobs.find((j) => j.action === 'backfill_embeddings')
  )
  const cancelJob = useActivityStore((s) => s.cancel)
  const refreshActivity = useActivityStore((s) => s.refresh)
  const backfillRunning = backfillJob ? isActive(backfillJob) : false

  // When the run ends, the count is stale by exactly the amount the run just fixed.
  useEffect(() => {
    if (backfillJob && !isActive(backfillJob)) loadCoverage()
  }, [backfillJob, loadCoverage])

  const startBackfill = async () => {
    setError(null)
    try {
      await embeddingsApi.backfill()
      await refreshActivity()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not start embedding'))
    }
  }

  return (
    <section className="card space-y-4 p-5">
      <header className="flex items-center gap-2">
        <Search className="h-4 w-4 text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Semantic search
        </h2>
      </header>

      <p className="text-xs text-gray-600 dark:text-gray-400">
        Embeddings are what let you find a moment by describing it rather than by quoting
        it. Without a provider here, search still works but matches words only.
      </p>

      {settings && (
        <p
          className={
            settings.configured
              ? 'flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400'
              : 'text-xs text-amber-700 dark:text-amber-400'
          }
        >
          {settings.configured ? (
            <>
              <Check className="h-3.5 w-3.5" />
              Semantic search is on.
            </>
          ) : (
            'Semantic search is off — add a key below to turn it on.'
          )}
        </p>
      )}

      {error && (
        <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </p>
      )}

      {settings && (
        <div>
          <label className="label" htmlFor="embedding-provider">
            Provider
          </label>
          <select
            id="embedding-provider"
            className="input"
            value={settings.provider}
            onChange={(e) => save({ provider: e.target.value })}
          >
            {settings.available_providers.map((provider) => (
              <option key={provider} value={provider}>
                {provider === 'openai' ? 'OpenAI' : 'Ollama (runs locally)'}
              </option>
            ))}
          </select>
          <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
            {isOpenAI
              ? 'Text is sent to OpenAI to be embedded.'
              : 'Nothing leaves this machine. Ollama must be running and have the model pulled.'}
          </p>
        </div>
      )}

      {settings && isOpenAI && (
        <div>
          <label className="label" htmlFor="openai-key">
            OpenAI API key
          </label>
          <div className="flex gap-2">
            <input
              id="openai-key"
              type="password"
              className="input flex-1"
              placeholder={
                settings.openai_key_configured ? 'A key is configured' : 'Paste your key'
              }
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              autoComplete="off"
            />
            <button
              type="button"
              className="btn btn-primary"
              disabled={!keyInput.trim() || saving}
              onClick={() => save({ openai_api_key: keyInput })}
              aria-label="Save OpenAI key"
            >
              Save
            </button>
          </div>
          {settings.openai_key_configured && (
            <button
              type="button"
              className="mt-1.5 text-xs text-red-600 hover:underline dark:text-red-400"
              onClick={() => save({ openai_api_key: '' })}
            >
              Remove the stored key
            </button>
          )}
        </div>
      )}

      {settings && !isOpenAI && (
        <div>
          <label className="label" htmlFor="ollama-url">
            Ollama address
          </label>
          <div className="flex gap-2">
            <input
              id="ollama-url"
              type="url"
              className="input flex-1"
              placeholder="http://localhost:11434"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-primary"
              disabled={saving}
              onClick={() => save({ ollama_base_url: urlInput })}
              aria-label="Save Ollama address"
            >
              Save
            </button>
          </div>
        </div>
      )}

      {settings && (
        <div>
          <label className="label" htmlFor="embedding-model">
            Model
          </label>
          <select
            id="embedding-model"
            className="input"
            value={settings.model}
            onChange={(e) => save({ model: e.target.value })}
          >
            {settings.available_models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
              </option>
            ))}
          </select>
          <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
            Changing the model leaves existing vectors behind — they are stored with the
            model that produced them, and only ones that match are searched.
          </p>
        </div>
      )}

      {coverage && coverage.pending_assets > 0 && (
        <div className="space-y-2 border-t border-gray-100 pt-3 dark:border-gray-800">
          <p className="text-xs text-gray-600 dark:text-gray-400">
            {coverage.pending_assets.toLocaleString()} of{' '}
            {coverage.total_assets.toLocaleString()} assets have no embeddings for this
            model
            {coverage.pending_segments > 0 &&
              `, covering ${coverage.pending_segments.toLocaleString()} transcript segments`}
            . Until they do, only the ones added since you configured a provider can be
            found by meaning.
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {isOpenAI
              ? 'That text is sent to OpenAI. GAM does not track spend — check your provider dashboard.'
              : 'Nothing leaves this machine.'}
          </p>

          {backfillRunning && backfillJob ? (
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-gray-600 dark:text-gray-400">
                {backfillJob.stalled
                  ? 'Not responding'
                  : backfillJob.detail || backfillJob.stage || 'Working'}
                {backfillJob.progress > 0 && ` · ${backfillJob.progress}%`}
              </p>
              <button
                type="button"
                className="btn btn-ghost text-xs"
                onClick={() => void cancelJob(backfillJob)}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void startBackfill()}
            >
              Embed {coverage.pending_assets.toLocaleString()}{' '}
              {coverage.pending_assets === 1 ? 'asset' : 'assets'}
            </button>
          )}
        </div>
      )}

      {coverage && coverage.pending_assets === 0 && coverage.total_assets > 0 && (
        <p className="flex items-center gap-1.5 border-t border-gray-100 pt-3 text-xs text-green-700 dark:border-gray-800 dark:text-green-400">
          <Check className="h-3.5 w-3.5" />
          Everything is embedded.
        </p>
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

export default function SettingsView() {
  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-6">
      <Link
        to="/library"
        className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to library
      </Link>

      <EmbeddingPanel />
      <SpeechPanel />
    </div>
  )
}
