import client from '@/api/client'
import type { ActivityJob } from '@/api/transcripts'

/**
 * Running an AI enrichment pass over one asset.
 *
 * Grouped by feature rather than by router, the way `api/embeddings.ts` posts to
 * `/assets/{id}/embed`: the endpoints live on the asset, but what they have in common is
 * the provider behind them.
 */

interface DataResponse<T> {
  data: T
}

interface ListResponse<T> {
  data: T[]
  total: number
}

/** What a run proposed. Never applied until someone accepts it (FR 9.1.4). */
export interface Suggestion {
  id: string
  asset_id: string
  kind: 'tag' | 'title'
  value: string
  status: 'pending' | 'accepted' | 'rejected'
}

/** What a whole selection can be put through. Transcription is deliberately absent —
 *  it is billed per minute of audio, and a mis-click over two hundred videos is an
 *  expensive way to discover it was on the menu. Extracting text is here for the
 *  opposite reason: it calls nothing, and documents arrive by the folder. */
export type BulkAction = 'describe' | 'summarize' | 'autotag' | 'embed' | 'extract_text'

/** One readable chunk of a document, in the unit that document naturally has. */
export interface DocumentPage {
  id: string
  idx: number
  page_number: number | null
  label: string | null
  text: string
}

export const enrichmentApi = {
  summarize(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/summarize`)
      .then((r) => r.data.data)
  },

  describe(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/describe`)
      .then((r) => r.data.data)
  },

  /** Summarise, describe and autotag together — the "Generate all" button. */
  generateAll(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/generate-all`)
      .then((r) => r.data.data)
  },

  autotag(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/autotag`)
      .then((r) => r.data.data)
  },

  extractText(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/extract-text`)
      .then((r) => r.data.data)
  },

  text(assetId: string): Promise<DocumentPage[]> {
    return client
      .get<ListResponse<DocumentPage>>(`/assets/${assetId}/text`)
      .then((r) => r.data.data)
  },

  suggestions(assetId: string): Promise<Suggestion[]> {
    return client
      .get<ListResponse<Suggestion>>(`/assets/${assetId}/suggestions`)
      .then((r) => r.data.data)
  },

  accept(assetId: string, suggestionId: string): Promise<Suggestion> {
    return client
      .post<DataResponse<Suggestion>>(
        `/assets/${assetId}/suggestions/${suggestionId}/accept`
      )
      .then((r) => r.data.data)
  },

  bulk(action: BulkAction, assetIds: string[]): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>('/assets/bulk/enrich', {
        action,
        asset_ids: assetIds,
      })
      .then((r) => r.data.data)
  },

  reject(assetId: string, suggestionId: string): Promise<Suggestion> {
    return client
      .post<DataResponse<Suggestion>>(
        `/assets/${assetId}/suggestions/${suggestionId}/reject`
      )
      .then((r) => r.data.data)
  },
}
