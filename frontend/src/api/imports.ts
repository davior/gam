import client from '@/api/client'
import type { ActivityJob } from '@/api/transcripts'

/**
 * Importing from a link — YouTube and the other sites yt-dlp knows — over
 * `routers/imports.py`.
 *
 * Returns the queued job rather than an asset: reading the page, downloading and
 * cataloguing all happen in the background, and a playlist becomes many assets. The
 * finished job names what it made in `result_asset_id`, the same field a sub-video
 * extraction uses.
 */

export interface UrlImportRequest {
  url: string
  /** Just the soundtrack, as .m4a. */
  audio_only?: boolean
  /** On by default. Off sends the uploader's tags to the suggestion queue instead. */
  apply_tags?: boolean
  /** One non-destructive clip per chapter, when the video has chapters. */
  chapters_as_clips?: boolean
}

interface DataResponse<T> {
  data: T
}

export const importsApi = {
  fromUrl(request: UrlImportRequest): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>('/assets/import', request)
      .then((r) => r.data.data)
  },
}
