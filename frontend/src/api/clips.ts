import client from '@/api/client'
import type { Asset } from '@/api/assets'
import type { ActivityJob } from '@/api/transcripts'

/**
 * Clips (non-destructive), and new files cut or re-encoded from an asset, over
 * `routers/clips.py`.
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

/** The frame's short side in pixels: 1080 is 1920×1080 landscape or 1080×1920
 *  portrait. Mirrors `enrichment/reencode.py::TARGETS`. */
export const REENCODE_TARGETS = [2160, 1440, 1080, 720, 480, 360] as const

export function reencodeLabel(shortSide: number): string {
  return shortSide === 2160 ? '4K' : `${shortSide}p`
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

  /** Re-trims a live clip — from its parent's Clip tab or its own Trim tab. A default
   *  name carrying the old range is renamed to the new one; a chosen name is kept. */
  setRange(
    clipId: string,
    range: { in_point: number; out_point: number }
  ): Promise<Asset> {
    return client
      .put<DataResponse<Asset>>(`/assets/${clipId}/range`, range)
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
   *  one-click path, and the Clip tab's per-row "Extract as file". `clipId` is the
   *  clip's own id, not its parent's. */
  promote(clipId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${clipId}/promote`)
      .then((r) => r.data.data)
  },

  /** Queues a smaller copy as a new asset, carrying the original's tags, attribution
   *  and transcript so the original can be deleted afterwards. Its id lands on the
   *  finished job as `result_asset_id`. */
  reencode(assetId: string, shortSide: number): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/reencode`, {
        short_side: shortSide,
      })
      .then((r) => r.data.data)
  },
}
