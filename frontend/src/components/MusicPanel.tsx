import { useEffect, useMemo, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { AudioWaveform, Check } from 'lucide-react'
import { MUSIC_ENERGY_LEVELS } from '@/api/assets'
import type { Asset, AssetUpdate, AudioKind, MusicEnergy } from '@/api/assets'
import { enrichmentApi } from '@/api/enrichment'
import EnrichmentButton from '@/components/EnrichmentButton'
import { useLibraryStore } from '@/stores/library'
import { splitTerms } from '@/utils/terms'
import { useSavedFlash } from '@/utils/useSavedFlash'

/**
 * What a track sounds like, so Video Creator can find music that fits a scene.
 *
 * Whatever the file's own tags said (ID3 genre, BPM, key, mood) is already filled in
 * by the time this opens; this is where the rest gets typed and where a wrong tag gets
 * corrected. Genres, moods and instruments are comma-separated text rather than chip
 * inputs: a list of three short words is quicker to type than to click together, and a
 * tag writer's "Ambient; Cinematic" pastes straight in.
 */

const LIST_FIELDS = ['music_genres', 'music_moods', 'music_instruments'] as const
type ListField = (typeof LIST_FIELDS)[number]

const LIST_LABELS: Record<ListField, { label: string; placeholder: string }> = {
  music_genres: { label: 'Genres', placeholder: 'Ambient, cinematic, folk' },
  music_moods: { label: 'Moods', placeholder: 'Tense, uplifting, melancholy' },
  music_instruments: { label: 'Instruments', placeholder: 'Piano, strings, synth' },
}

/** The same bounds the server enforces (`app/music.py`). Outside them a "BPM" is a
 *  track number or a year in the wrong field, not a tempo. */
const MIN_BPM = 20
const MAX_BPM = 300

type Vocals = '' | 'vocals' | 'instrumental'

interface Draft {
  music_genres: string
  music_moods: string
  music_instruments: string
  music_bpm: string
  music_key: string
  music_vocals: Vocals
  music_energy: '' | MusicEnergy
  audio_kind: '' | AudioKind
}

const KIND_LABELS: Record<AudioKind, string> = {
  music: 'Music',
  speech: 'Speech',
  sfx: 'Sound effect',
}

function draftFrom(asset: Asset): Draft {
  return {
    music_genres: asset.music_genres.join(', '),
    music_moods: asset.music_moods.join(', '),
    music_instruments: asset.music_instruments.join(', '),
    music_bpm: asset.music_bpm === null ? '' : String(asset.music_bpm),
    music_key: asset.music_key ?? '',
    music_vocals:
      asset.music_vocals === null ? '' : asset.music_vocals ? 'vocals' : 'instrumental',
    music_energy: asset.music_energy ?? '',
    audio_kind: asset.audio_kind ?? '',
  }
}

function bpmValid(value: string): boolean {
  const trimmed = value.trim()
  if (!trimmed) return true
  const bpm = Number(trimmed)
  return Number.isFinite(bpm) && bpm >= MIN_BPM && bpm <= MAX_BPM
}

interface Props {
  asset: Asset
}

export default function MusicPanel({ asset }: Props) {
  const update = useLibraryStore((s) => s.update)
  const [draft, setDraft] = useState<Draft>(() => draftFrom(asset))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, flashSaved] = useSavedFlash()

  // Re-seed on a different asset or a server-side change to this one, not on every new
  // object the store hands back — see AttributionPanel for why identity is the wrong key.
  useEffect(() => {
    setDraft(draftFrom(asset))
    setError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asset.id, asset.metadata_modified_date])

  const dirty = useMemo(() => {
    const current = draftFrom(asset)
    return (Object.keys(current) as (keyof Draft)[]).some(
      (key) => draft[key] !== current[key]
    )
  }, [asset, draft])

  const tempoValid = bpmValid(draft.music_bpm)

  const set = <K extends keyof Draft>(name: K, value: Draft[K]) =>
    setDraft((previous) => ({ ...previous, [name]: value }))

  const save = async () => {
    if (!dirty || !tempoValid) return
    setSaving(true)
    setError(null)
    try {
      // Only what changed: the API stamps every key it receives as human-written, and a
      // BPM read from the file's ID3 frame should keep saying so until someone edits it.
      const current = draftFrom(asset)
      const changes: AssetUpdate = {}
      for (const field of LIST_FIELDS) {
        if (draft[field] !== current[field]) changes[field] = splitTerms(draft[field])
      }
      if (draft.music_bpm !== current.music_bpm) {
        changes.music_bpm = draft.music_bpm.trim() ? Number(draft.music_bpm) : null
      }
      if (draft.music_key !== current.music_key) {
        changes.music_key = draft.music_key.trim() || null
      }
      if (draft.music_vocals !== current.music_vocals) {
        changes.music_vocals =
          draft.music_vocals === '' ? null : draft.music_vocals === 'vocals'
      }
      if (draft.music_energy !== current.music_energy) {
        changes.music_energy = draft.music_energy || null
      }
      if (draft.audio_kind !== current.audio_kind) {
        changes.audio_kind = draft.audio_kind || null
      }

      await update(asset.id, changes)
      flashSaved()
    } catch {
      setError('Could not save. Your changes are still here.')
    } finally {
      setSaving(false)
    }
  }

  // Escape must not reach DetailDock's window listener, which closes the whole panel.
  const swallowEscape = (event: KeyboardEvent) => {
    if (event.key === 'Escape') event.stopPropagation()
  }

  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4" onKeyDown={swallowEscape}>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        What this track sounds like, so Video Creator can find music that fits a scene.
        Anything the file&apos;s own tags said is already filled in.
      </p>

      <div>
        <label className="label" htmlFor="music-audio_kind">
          This is
        </label>
        <select
          id="music-audio_kind"
          className="input"
          value={draft.audio_kind}
          onChange={(e) => set('audio_kind', e.target.value as Draft['audio_kind'])}
        >
          <option value="">Not known</option>
          {(Object.keys(KIND_LABELS) as AudioKind[]).map((kind) => (
            <option key={kind} value={kind}>
              {KIND_LABELS[kind]}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Speech, and anything not known, is transcribed. Music and sound effects are not;
          marking a file as speech transcribes it.
        </p>
      </div>

      {/* Measures from the sound and fills only what is blank, so it can never undo a
          correction. Tempo, key and energy only: vocals, genre and mood need a person. */}
      {!asset.parent_asset_id && (
        <EnrichmentButton
          assetId={asset.id}
          action="analyze_music"
          icon={AudioWaveform}
          label="Measure tempo, key and energy"
          runningLabel="Listening…"
          start={enrichmentApi.analyzeMusic}
          failureMessage="Could not start the analysis"
        />
      )}
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Measuring fills only empty fields. Clear one first to measure it again.
      </p>

      {LIST_FIELDS.map((field) => (
        <div key={field}>
          <label className="label" htmlFor={`music-${field}`}>
            {LIST_LABELS[field].label}
          </label>
          <input
            id={`music-${field}`}
            className="input"
            value={draft[field]}
            placeholder={LIST_LABELS[field].placeholder}
            onChange={(e) => set(field, e.target.value)}
          />
        </div>
      ))}

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="music-music_bpm">
            Tempo (BPM)
          </label>
          <input
            id="music-music_bpm"
            className="input"
            inputMode="decimal"
            value={draft.music_bpm}
            placeholder="e.g. 92"
            aria-invalid={!tempoValid}
            onChange={(e) => set('music_bpm', e.target.value)}
          />
          {!tempoValid && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">
              A tempo between {MIN_BPM} and {MAX_BPM}.
            </p>
          )}
        </div>
        <div>
          <label className="label" htmlFor="music-music_key">
            Key
          </label>
          <input
            id="music-music_key"
            className="input"
            value={draft.music_key}
            maxLength={16}
            placeholder="Am, A minor, 8A"
            onChange={(e) => set('music_key', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="music-music_vocals">
            Vocals
          </label>
          <select
            id="music-music_vocals"
            className="input"
            value={draft.music_vocals}
            onChange={(e) => set('music_vocals', e.target.value as Vocals)}
          >
            <option value="">Not known</option>
            <option value="instrumental">Instrumental</option>
            <option value="vocals">Has vocals</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="music-music_energy">
            Energy
          </label>
          <select
            id="music-music_energy"
            className="input"
            value={draft.music_energy}
            onChange={(e) => set('music_energy', e.target.value as Draft['music_energy'])}
          >
            <option value="">Not known</option>
            {MUSIC_ENERGY_LEVELS.map((level) => (
              <option key={level} value={level}>
                {level[0].toUpperCase() + level.slice(1)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void save()}
          disabled={!dirty || saving || !tempoValid}
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
