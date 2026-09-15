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

export const enrichmentApi = {
  summarize(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/summarize`)
      .then((r) => r.data.data)
  },
}
