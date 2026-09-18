import client from '@/api/client'
import type { Asset } from '@/api/assets'
import type { ActivityJob } from '@/api/transcripts'

/**
 * Clips (non-destructive) and sub-video extraction (destructive), over `routers/clips.py`.
 *
 * One `xApi` per backend router, the house convention — `Asset`/`ActivityJob` are
 * imported rather than redeclared, since `api/assets.ts` and `api/transcripts.ts`
 * already own those shapes.
 */

export interface ClipRange {
  in_point: number
  out_point: number
  name?: string
}

interface DataResponse<T> {
  data: T
}

interface ListResponse<T> {
  data: T[]
  total: number
}

export const clipsApi = {
  /** A window into the parent's bytes: no file, no job — resolves immediately. */
  create(assetId: string, range: ClipRange): Promise<Asset> {
    return client
      .post<DataResponse<Asset>>(`/assets/${assetId}/clips`, range)
      .then((r) => r.data.data)
  },

  /** Everything derived from this asset: live clips and past promotions/extractions —
   *  filter to `source === 'clip'` for just the ones still depending on it. */
  list(assetId: string): Promise<Asset[]> {
    return client
      .get<ListResponse<Asset>>(`/assets/${assetId}/clips`)
      .then((r) => r.data.data)
  },

  /** Queues a fresh, standalone extraction. The source asset is untouched; the new
   *  asset's id lands on the finished job as `result_asset_id`. */
  extractSubvideo(assetId: string, range: ClipRange): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/subvideo`, range)
      .then((r) => r.data.data)
  },

  /** Turns a live clip into a standalone sub-video, in place — the delete guard's
   *  one-click path. `clipId` is the clip's own id, not its parent's. */
  promote(clipId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${clipId}/promote`)
      .then((r) => r.data.data)
  },
}
