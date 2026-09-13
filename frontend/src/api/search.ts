import client from '@/api/client'
import type { Asset } from '@/api/assets'

/** Search across the library — names, descriptions and every spoken word. */

export interface SearchHit {
  asset: Asset
  score: number
  /** The matched excerpt. Keyword hits wrap matched words in «guillemets». */
  snippet: string
  /** Seconds into the asset. Null for a metadata hit, which is about the file itself. */
  start_time: number | null
  segment_id: string | null
  /** Which retrievers found it: 'keyword', 'semantic', or both. */
  sources: string[]
  /** Other moments in the same asset that also matched. */
  other_matches: number
}

export interface SearchResponse {
  data: SearchHit[]
  total: number
  query: string
  /** False when no embedding provider is configured, or when one failed. */
  semantic: boolean
  semantic_error: string | null
}

export const searchApi = {
  run(
    q: string,
    params: { asset_type?: string; limit?: number } = {}
  ): Promise<SearchResponse> {
    return client
      .get<SearchResponse>('/search', { params: { q, ...params } })
      .then((r) => r.data)
  },
}

/**
 * Split a snippet on the «guillemets» the backend wraps matches in.
 *
 * Returned as parts rather than HTML: building markup here and injecting it with
 * dangerouslySetInnerHTML would make a transcript — text this app did not write — into
 * a scripting vector. Guillemets are used as the marker precisely because they are
 * vanishingly rare in transcribed speech.
 */
export function splitHighlights(
  snippet: string
): Array<{ text: string; match: boolean }> {
  if (!snippet) return []

  const parts: Array<{ text: string; match: boolean }> = []
  const pattern = /«([^»]*)»/g
  let cursor = 0
  let found: RegExpExecArray | null

  while ((found = pattern.exec(snippet)) !== null) {
    if (found.index > cursor) {
      parts.push({ text: snippet.slice(cursor, found.index), match: false })
    }
    parts.push({ text: found[1], match: true })
    cursor = found.index + found[0].length
  }

  if (cursor < snippet.length) {
    parts.push({ text: snippet.slice(cursor), match: false })
  }
  return parts
}
