import { useState, type FormEvent, type ReactNode } from 'react'
import { Link2, Loader2 } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import { importsApi } from '@/api/imports'
import { useActivityStore } from '@/stores/activity'

/**
 * Paste a link, get an asset — the other way into the library beside `UploadZone`.
 *
 * Owns no progress bar of its own: an import is a background job like any other, so the
 * activity indicator already shows it, and a playlist that fans out into forty downloads
 * has nowhere sensible to put forty bars here anyway. What arrives is added to the grid
 * by `LibraryView` when its job finishes.
 *
 * The input is `type="text"` rather than `type="url"`: the browser's url check refuses
 * `youtu.be/abc` without a scheme, which is how people paste links, and the server
 * already accepts and normalises it.
 */
export default function UrlImport() {
  const refreshActivity = useActivityStore((s) => s.refresh)

  const [url, setUrl] = useState('')
  const [audioOnly, setAudioOnly] = useState(false)
  const [applyTags, setApplyTags] = useState(true)
  const [chaptersAsClips, setChaptersAsClips] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [queued, setQueued] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const link = url.trim()
    if (!link || submitting) return

    setSubmitting(true)
    setError(null)
    setQueued(null)
    try {
      const job = await importsApi.fromUrl({
        url: link,
        audio_only: audioOnly,
        apply_tags: applyTags,
        chapters_as_clips: chaptersAsClips,
      })
      setQueued(job.asset_name || link)
      // Cleared so the next paste is not appended to this one. The options stay: someone
      // importing a run of talks as audio wants the box to stay ticked.
      setUrl('')
      void refreshActivity()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not start that import'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={(e) => void submit(e)}
      className="space-y-2 rounded-lg border border-gray-200 px-3 py-2.5 dark:border-gray-700"
      aria-label="Import from a link"
    >
      <div className="flex items-center gap-2">
        <Link2 className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
        <input
          className="input min-w-0 flex-1"
          type="text"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="Or paste a YouTube link (or Rumble, Odysee, Vimeo…)"
          aria-label="Link to import"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button
          type="submit"
          className="btn btn-primary px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!url.trim() || submitting}
        >
          {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Import'}
        </button>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 pl-6 text-xs text-gray-600 dark:text-gray-400">
        <Option checked={audioOnly} onChange={setAudioOnly}>
          Audio only
        </Option>
        <Option checked={applyTags} onChange={setApplyTags}>
          Apply the uploader&rsquo;s tags
        </Option>
        <Option checked={chaptersAsClips} onChange={setChaptersAsClips}>
          Chapters as clips
        </Option>
      </div>

      {queued && (
        <p role="status" className="pl-6 text-xs text-green-700 dark:text-green-400">
          Queued &ldquo;{queued}&rdquo; — follow it under background activity.
        </p>
      )}
      {error && (
        <p role="alert" className="pl-6 text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </form>
  )
}

function Option({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  children: ReactNode
}) {
  return (
    <label className="flex items-center gap-1.5">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {children}
    </label>
  )
}
