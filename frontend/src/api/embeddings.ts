import client from '@/api/client'
import type { ActivityJob } from '@/api/transcripts'

/** Semantic-search coverage, and filling it in. */

interface DataResponse<T> {
  data: T
}

export interface EmbeddingCoverage {
  model: string
  total_assets: number
  embedded_assets: number
  pending_assets: number
  /** The real unit of work — one vector each, and a long interview holds thousands. */
  pending_segments: number
}

export const embeddingsApi = {
  status(): Promise<EmbeddingCoverage> {
    return client
      .get<DataResponse<EmbeddingCoverage>>('/embeddings/status')
      .then((r) => r.data.data)
  },

  backfill(): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>('/embeddings/backfill')
      .then((r) => r.data.data)
  },

  /**
   * Embed one asset.
   *
   * Served by the transcripts router rather than the embeddings one, which bends the
   * one-module-per-router convention. Recorded rather than hidden: the endpoint lives
   * beside transcription because that is where the chain that queues it lives, and
   * grouping this call by feature beats splitting embedding across two api modules.
   */
  embedAsset(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/embed`)
      .then((r) => r.data.data)
  },
}
