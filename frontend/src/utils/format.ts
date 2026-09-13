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
