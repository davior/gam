/** Human-readable renderings of the numbers an asset carries. */

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB']

export function formatBytes(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B'

  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  // Whole bytes read oddly with a decimal; everything else gets one.
  return `${unit === 0 ? value : value.toFixed(1)} ${UNITS[unit]}`
}

/**
 * Seconds as a timestamp.
 *
 * Hours only when there are hours, so a 12-second clip is "0:12" rather than
 * "00:00:12" — this is shown on every grid tile and the padding is noise.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (
    seconds === null ||
    seconds === undefined ||
    !Number.isFinite(seconds) ||
    seconds < 0
  ) {
    return ''
  }

  const whole = Math.floor(seconds)
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const secs = whole % 60

  const paddedSeconds = String(secs).padStart(2, '0')
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${paddedSeconds}`
  }
  return `${minutes}:${paddedSeconds}`
}

export function formatDimensions(width: number | null, height: number | null): string {
  if (!width || !height) return ''
  return `${width} × ${height}`
}

/** A date as a short absolute string. Relative times are worse for a library: "3
 * months ago" does not help you find the shoot you did in June. */
export function formatDate(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** A timecode's four fields, each as typed text, so a half-typed value survives. */
export interface TimecodeParts {
  hours: string
  minutes: string
  seconds: string
  millis: string
}

/**
 * Seconds as editable HH:MM:SS:mmm parts.
 *
 * Rounded to the millisecond once, as a whole, rather than per field: flooring the
 * seconds and rounding the remainder separately turns 1.9996 into "01:1000".
 */
export function toTimecodeParts(seconds: number | null): TimecodeParts {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) {
    return { hours: '', minutes: '', seconds: '', millis: '' }
  }
  const totalMs = Math.round(seconds * 1000)
  const pad = (n: number, width: number) => String(n).padStart(width, '0')
  return {
    hours: pad(Math.floor(totalMs / 3_600_000), 2),
    minutes: pad(Math.floor((totalMs % 3_600_000) / 60_000), 2),
    seconds: pad(Math.floor((totalMs % 60_000) / 1000), 2),
    millis: pad(totalMs % 1000, 3),
  }
}

/**
 * The parts back to seconds, or null if any field is not a whole number.
 *
 * Fields are not range-checked: 90 seconds is read as 1:30, the way a typist means
 * it, and the display normalises once the field is left.
 */
export function fromTimecodeParts(parts: TimecodeParts): number | null {
  const fields = [parts.hours, parts.minutes, parts.seconds, parts.millis]
  // An empty field is zero, so typing just the seconds of a fresh point works.
  if (fields.some((f) => f.trim() !== '' && !/^\d+$/.test(f.trim()))) return null
  const [h, m, s, ms] = fields.map((f) => (f.trim() === '' ? 0 : Number(f)))
  return (h * 3_600_000 + m * 60_000 + s * 1000 + ms) / 1000
}

/** Seconds as a fixed-width HH:MM:SS:mmm, for anywhere a precise point is shown. */
export function formatTimecode(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return ''
  const p = toTimecodeParts(seconds)
  if (!p.hours) return ''
  return `${p.hours}:${p.minutes}:${p.seconds}:${p.millis}`
}
