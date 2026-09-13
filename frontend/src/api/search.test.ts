import { describe, expect, it } from 'vitest'
import { splitHighlights } from '@/api/search'

/**
 * The backend marks matches with «guillemets» and the UI turns them into <mark>
 * elements. Parsing to parts rather than to HTML is deliberate: a transcript is text
 * this app did not write, and building markup from it for dangerouslySetInnerHTML would
 * make every hostile filename and every mis-transcribed word a scripting vector.
 */
describe('splitHighlights', () => {
  it('separates matched words from their surroundings', () => {
    expect(splitHighlights('deploying «nano» «weapons» via aerosol')).toEqual([
      { text: 'deploying ', match: false },
      { text: 'nano', match: true },
      { text: ' ', match: false },
      { text: 'weapons', match: true },
      { text: ' via aerosol', match: false },
    ])
  })

  it('handles a match at the very start and very end', () => {
    expect(splitHighlights('«nano» weapons')[0]).toEqual({ text: 'nano', match: true })
    const trailing = splitHighlights('deploying «nano»')
    expect(trailing[trailing.length - 1]).toEqual({ text: 'nano', match: true })
  })

  it('returns plain text unchanged when nothing matched', () => {
    expect(splitHighlights('no markers here')).toEqual([
      { text: 'no markers here', match: false },
    ])
  })

  it('is empty for an empty snippet', () => {
    expect(splitHighlights('')).toEqual([])
  })

  it('does not treat markup in the text as markup', () => {
    // The parts carry the literal characters; React escapes them on render. Nothing
    // here ever becomes HTML.
    const parts = splitHighlights('a «<script>alert(1)</script>» b')
    expect(parts[1]).toEqual({ text: '<script>alert(1)</script>', match: true })
  })

  it('leaves an unclosed marker alone rather than swallowing the rest', () => {
    expect(splitHighlights('deploying «nano weapons')).toEqual([
      { text: 'deploying «nano weapons', match: false },
    ])
  })
})
