import client from '@/api/client'
import type { Asset } from '@/api/assets'
import type { ActivityJob } from '@/api/transcripts'

/**
 * A video's standard-resolution versions, over `routers/versions.py`: the 1080p, the
 * 720p, beside the original. Files of the one asset, not assets of their own.
 */

export interface AssetVersion {
  id: string
  asset_id: string
  /** The standard size it was made as: the frame's short side in pixels. */
  short_side: number
  width: number | null
  height: number | null
  size_bytes: number
  codec: string | null
  mime_type: string | null
  file_url: string | null
  created_at: string
}

/** The frame's short side in pixels: 1080 is 1920×1080 landscape or 1080×1920
 *  portrait. Mirrors `enrichment/versions.py::SIZES`. */
export const VERSION_SIZES = [2160, 1440, 1080, 720, 480, 360] as const

export function versionLabel(shortSide: number): string {
  return shortSide === 2160 ? '4K' : `${shortSide}p`
}

/** A video or sound that owns a file and has no length stored: what a browser's
 *  live-stream recording looks like in the library. A clip owns no file of its own (it is
 *  a window onto its parent's), so there is nothing of its own to rewrite. Goes false once
 *  the repair has given the file a length. */
export function needsLengthRepair(asset: Asset): boolean {
  return (
    ['video', 'audio'].includes(asset.asset_type) &&
    asset.source !== 'clip' &&
    !asset.missing &&
    !asset.duration_seconds
  )
}

interface DataResponse<T> {
  data: T
}

interface ListResponse<T> {
  data: T[]
  total: number
}

export const versionsApi = {
  /** Largest first. */
  list(assetId: string): Promise<AssetVersion[]> {
    return client
      .get<ListResponse<AssetVersion>>(`/assets/${assetId}/versions`)
      .then((r) => r.data.data)
  },

  /** Queues one size. Only one is made at a time per video. */
  create(assetId: string, shortSide: number): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/versions`, {
        short_side: shortSide,
      })
      .then((r) => r.data.data)
  },

  /** Rewrites a recording's file so it carries a length and can be seeked: what a
   *  browser's live-stream WebM lacks. Nothing is re-encoded, and the asset keeps its
   *  id. Queues a job; one at a time per asset. */
  remux(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/remux`)
      .then((r) => r.data.data)
  },

  remove(assetId: string, versionId: string): Promise<void> {
    return client.delete(`/assets/${assetId}/versions/${versionId}`).then(() => undefined)
  },

  /** The version's file becomes the asset's own and the original is deleted.
   *  Irreversible. Resolves with the asset as it now is. */
  makeOriginal(assetId: string, versionId: string): Promise<Asset> {
    return client
      .post<DataResponse<Asset>>(`/assets/${assetId}/versions/${versionId}/make-original`)
      .then((r) => r.data.data)
  },
}
