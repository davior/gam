import { useEffect, useState } from 'react'
import type { Asset } from '@/api/assets'
import { apiErrorMessage } from '@/api/client'
import { useLibraryStore } from '@/stores/library'
import TimecodeInput from '@/components/TimecodeInput'

/**
 * Re-trimming a live clip that already exists.
 *
 * Shared by the parent's Clip tab (a row's "Edit times") and the clip's own Trim tab,
 * so the two can never disagree about what editing a range means. Both play the
 * parent's bytes, so `currentTime` is in the parent's timeline either way and "set to
 * the playhead" means the same thing on both pages.
 */

interface Props {
  clip: Asset
  currentTime: number
  onSeek: (seconds: number) => void
  /** The parent's length, when known, to stop a nudge running off its end. */
  max?: number
  onSaved?: (clip: Asset) => void
  onCancel?: () => void
}

export default function ClipRangeForm({
  clip,
  currentTime,
  onSeek,
  max,
  onSaved,
  onCancel,
}: Props) {
  const [inPoint, setInPoint] = useState<number | null>(clip.in_point)
  const [outPoint, setOutPoint] = useState<number | null>(clip.out_point)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const setClipRange = useLibraryStore((s) => s.setClipRange)

  // Re-seed from the saved range when it changes underneath — a save from the other
  // page, or a different clip opening in the same panel.
  useEffect(() => {
    setInPoint(clip.in_point)
    setOutPoint(clip.out_point)
    setError(null)
  }, [clip.id, clip.in_point, clip.out_point])

  const rangeValid = inPoint !== null && outPoint !== null && outPoint > inPoint
  const dirty = inPoint !== clip.in_point || outPoint !== clip.out_point

  const save = async () => {
    if (inPoint === null || outPoint === null || !rangeValid) return
    setSaving(true)
    setError(null)
    try {
      // Not `onSaved?.(await …)`: an optional call skips evaluating its arguments, so
      // without an `onSaved` (the Trim tab passes none) the save would never be sent.
      const saved = await setClipRange(clip.id, inPoint, outPoint)
      onSaved?.(saved)
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not change the clip times'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-2.5">
      <TimecodeInput
        label="In point"
        value={inPoint}
        onChange={setInPoint}
        max={max}
        currentTime={currentTime}
        onSeek={onSeek}
      />
      <TimecodeInput
        label="Out point"
        value={outPoint}
        onChange={setOutPoint}
        max={max}
        currentTime={currentTime}
        onSeek={onSeek}
      />
      {!rangeValid && (
        <p className="text-[11px] text-amber-700 dark:text-amber-400">
          The out point must be after the in point.
        </p>
      )}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn btn-primary flex-1 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!dirty || !rangeValid || saving}
          onClick={() => void save()}
        >
          {saving ? 'Saving…' : dirty ? 'Save times' : 'Saved'}
        </button>
        {dirty && (
          <button
            type="button"
            className="btn btn-secondary px-2 py-1.5 text-xs"
            disabled={saving}
            onClick={() => {
              setInPoint(clip.in_point)
              setOutPoint(clip.out_point)
            }}
          >
            Revert
          </button>
        )}
        {onCancel && (
          <button
            type="button"
            className="btn btn-secondary px-2 py-1.5 text-xs"
            disabled={saving}
            onClick={onCancel}
          >
            Close
          </button>
        )}
      </div>
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  )
}
