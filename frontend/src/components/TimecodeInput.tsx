import { useEffect, useRef, useState } from 'react'
import { Crosshair, Play } from 'lucide-react'
import { fromTimecodeParts, toTimecodeParts, type TimecodeParts } from '@/utils/format'

/** The nudge buttons, smallest step innermost. Frames are 33–42 ms at common rates,
 *  so 10 ms is fine enough to land on one without a hundred presses. */
const NUDGES = [
  { delta: -1, label: '−1s' },
  { delta: -0.01, label: '−10ms' },
  { delta: 0.01, label: '+10ms' },
  { delta: 1, label: '+1s' },
]

const FIELDS: { key: keyof TimecodeParts; name: string; width: string; max: number }[] = [
  { key: 'hours', name: 'hours', width: 'w-9', max: 99 },
  { key: 'minutes', name: 'minutes', width: 'w-9', max: 59 },
  { key: 'seconds', name: 'seconds', width: 'w-9', max: 59 },
  { key: 'millis', name: 'milliseconds', width: 'w-12', max: 999 },
]

interface Props {
  /** "In point" or "Out point": names the fields and buttons for a screen reader. */
  label: string
  value: number | null
  onChange: (seconds: number) => void
  /** The end of the media, when known. The server checks it either way. */
  max?: number
  /** Where the player is; "set to playhead" copies it. */
  currentTime: number
  onSeek: (seconds: number) => void
  /** The set-to-playhead button's accessible name — "Set in point" in the Clip tab,
   *  which predates this component and is what its tests and users already know. */
  setLabel?: string
}

/** Rounded to the millisecond, the precision the fields show: without it, ten +10 ms
 *  nudges from 0 land on 0.09999999999999999 and the display drifts a millisecond. */
function clamp(seconds: number, max: number | undefined): number {
  const bounded = Math.max(0, max === undefined ? seconds : Math.min(seconds, max))
  return Math.round(bounded * 1000) / 1000
}

/**
 * One point in time as HH:MM:SS:mmm, typed or nudged.
 *
 * The fields hold their own text while one is focused: re-deriving them from `value`
 * on every keystroke would turn "7" then "75" seconds into "01:15" under the cursor.
 * They are normalised from `value` once focus leaves, and whenever `value` changes
 * from outside (a nudge, the playhead) while none is being typed in.
 */
export default function TimecodeInput({
  label,
  value,
  onChange,
  max,
  currentTime,
  onSeek,
  setLabel = `Set ${label.toLowerCase()} to the playhead`,
}: Props) {
  const [draft, setDraft] = useState<TimecodeParts>(() => toTimecodeParts(value))
  const typing = useRef(false)

  useEffect(() => {
    if (!typing.current) setDraft(toTimecodeParts(value))
  }, [value])

  const edit = (key: keyof TimecodeParts, text: string) => {
    const next = { ...draft, [key]: text }
    setDraft(next)
    const seconds = fromTimecodeParts(next)
    if (seconds !== null) onChange(clamp(seconds, max))
  }

  const nudge = (delta: number) => onChange(clamp((value ?? currentTime) + delta, max))

  return (
    <div role="group" aria-label={label} className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="w-7 shrink-0 text-xs font-medium text-gray-600 dark:text-gray-400"
        >
          {label.split(' ')[0]}
        </span>
        <div className="flex items-center font-mono text-xs text-gray-400">
          {FIELDS.map((field, i) => (
            <span key={field.key} className="flex items-center">
              {i > 0 && <span className="px-0.5">:</span>}
              <input
                type="number"
                inputMode="numeric"
                min={0}
                max={field.max}
                placeholder={field.key === 'millis' ? '000' : '00'}
                aria-label={`${label} ${field.name}`}
                className={`input ${field.width} px-1 py-1 text-center font-mono text-xs [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none`}
                value={draft[field.key]}
                onFocus={() => {
                  typing.current = true
                }}
                onBlur={() => {
                  typing.current = false
                  setDraft(toTimecodeParts(value))
                }}
                onChange={(e) => edit(field.key, e.target.value)}
              />
            </span>
          ))}
        </div>
        <button
          type="button"
          className="btn btn-ghost ml-auto p-1"
          onClick={() => onChange(clamp(currentTime, max))}
          aria-label={setLabel}
          title={setLabel}
        >
          <Crosshair className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className="btn btn-ghost p-1 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={value === null}
          onClick={() => value !== null && onSeek(value)}
          aria-label={`Play from the ${label.toLowerCase()}`}
          title={`Play from the ${label.toLowerCase()}`}
        >
          <Play className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex gap-1 pl-[2.125rem]">
        {NUDGES.map((n) => (
          <button
            key={n.label}
            type="button"
            className="btn btn-secondary flex-1 px-1 py-0.5 font-mono text-[11px]"
            onClick={() => nudge(n.delta)}
            aria-label={`${label} ${n.label}`}
          >
            {n.label}
          </button>
        ))}
      </div>
    </div>
  )
}
