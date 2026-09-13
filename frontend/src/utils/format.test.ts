import { describe, expect, it } from 'vitest'
import { formatBytes, formatDate, formatDimensions, formatDuration } from '@/utils/format'

describe('formatBytes', () => {
  it('scales to a readable unit', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatBytes(3.2 * 1024 ** 3)).toBe('3.2 GB')
  })

  it('does not put a decimal on whole bytes', () => {
    expect(formatBytes(1)).toBe('1 B')
  })

  it('handles nothing and nonsense', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(-1)).toBe('0 B')
  })
})

describe('formatDuration', () => {
  it('omits hours when there are none', () => {
    // Shown on every grid tile, so "00:00:12" would be padding noise.
    expect(formatDuration(12)).toBe('0:12')
    expect(formatDuration(95)).toBe('1:35')
  })

  it('includes hours for long recordings', () => {
    expect(formatDuration(3725)).toBe('1:02:05')
  })

  it('is empty rather than wrong when there is no duration', () => {
    expect(formatDuration(null)).toBe('')
    expect(formatDuration(undefined)).toBe('')
    expect(formatDuration(-5)).toBe('')
    expect(formatDuration(Number.NaN)).toBe('')
  })
})

describe('formatDimensions', () => {
  it('renders both or nothing', () => {
    expect(formatDimensions(1920, 1080)).toBe('1920 × 1080')
    expect(formatDimensions(1920, null)).toBe('')
    expect(formatDimensions(null, null)).toBe('')
  })
})

describe('formatDate', () => {
  it('is empty for an unparseable value rather than "Invalid Date"', () => {
    expect(formatDate('not a date')).toBe('')
  })

  it('renders a real timestamp', () => {
    expect(formatDate('2026-09-13T10:00:00Z')).not.toBe('')
  })
})
