import { useEffect, useState } from 'react'
import { ArrowLeft, Check, KeyRound } from 'lucide-react'
import { Link } from 'react-router-dom'
import { speechSettingsApi, type SpeechSettings } from '@/api/transcripts'
import { apiErrorMessage } from '@/api/client'

export default function SettingsView() {
  const [settings, setSettings] = useState<SpeechSettings | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
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
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-6">
      <Link
        to="/library"
        className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to library
      </Link>

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
          . Your key is encrypted before it is stored and is never sent back to the
          browser — this app makes the calls on your behalf.
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
                settings?.deepgram_key_configured
                  ? 'A key is configured'
                  : 'Paste your key'
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
    </div>
  )
}
