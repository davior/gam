import client from '@/api/client'
import type { Tag } from '@/api/tags'

/**
 * The asset library API.
 *
 * Types live here rather than in a shared `types/` directory — the convention carried
 * over from gecko-notes, where each api module owns the shapes it returns. `Tag` is
 * imported rather than redeclared for the same reason: the tag module owns that shape,
 * and a second copy here is a second thing to keep in step with the server.
 */

export type AssetType = 'image' | 'video' | 'audio' | 'document'

export interface Asset {
  id: string
  name: string
  description: string | null
  summary: string | null
  asset_type: AssetType
  source: string

  /** M7. Provenance on any of clip/promoted/extracted; only a live clip
   *  (`source === 'clip'`) has no file of its own and needs `in_point`/`out_point`
   *  to bound playback of its parent's bytes — a promoted or freshly-extracted
   *  sub-video keeps this as a breadcrumb but is a standalone asset in every other
   *  respect, `source` (not this) is what tells the two apart. */
  parent_asset_id: string | null
  in_point: number | null
  out_point: number | null

  original_name: string | null
  mime_type: string | null
  file_format: string | null
  size_bytes: number

  duration_seconds: number | null
  width: number | null
  height: number | null
  codec: string | null

  /** Signed and time-limited; minted per response, so never cache one. */
  file_url: string | null
  thumb_url: string | null

  /** The row exists but its bytes do not. */
  missing: boolean

  /** Batch-loaded for a page by the server, so reading this is free. */
  tags: Tag[]

  upload_date: string
  modified_date: string
  metadata_modified_date: string
}

export interface UploadRejection {
  filename: string
  code: string
  message: string
}

export interface UploadResult {
  created: Asset[]
  rejected: UploadRejection[]
}

/**
 * Every filter the listing understands.
 *
 * Named exactly as the query string names them, so the object passes straight to axios
 * with no mapping layer to drift. `tag` repeats and is ANDed by the server — picking a
 * second tag narrows, which is the only behaviour that makes a filter chip feel right.
 */
export interface ListAssetsParams {
  asset_type?: AssetType
  q?: string
  tag?: string[]
  category_id?: string
  source?: string
  min_duration?: number
  max_duration?: number
  uploaded_after?: string
  uploaded_before?: string
  limit?: number
  offset?: number
}

export interface AssetPage {
  data: Asset[]
  total: number
  limit: number
  offset: number
}

export interface AssetUpdate {
  name?: string
  description?: string | null
  summary?: string | null
}

interface DataResponse<T> {
  data: T
}

export const assetsApi = {
  list(params: ListAssetsParams = {}): Promise<AssetPage> {
    return client.get('/assets', { params }).then((r) => r.data)
  },

  get(id: string): Promise<Asset> {
    return client.get<DataResponse<Asset>>(`/assets/${id}`).then((r) => r.data.data)
  },

  /**
   * Upload one or more files.
   *
   * `onProgress` reports 0-1 for the whole batch. Video is large enough that an
   * upload with no feedback reads as a hang.
   */
  upload(files: File[], onProgress?: (fraction: number) => void): Promise<UploadResult> {
    const body = new FormData()
    files.forEach((file) => body.append('files', file))

    return client
      .post<UploadResult>('/assets', body, {
        // Deliberately not setting Content-Type: the browser has to add the multipart
        // boundary itself, and naming the type without it produces an unparseable body.
        headers: { 'Content-Type': undefined },
        onUploadProgress(event) {
          if (!onProgress || !event.total) return
          onProgress(event.loaded / event.total)
        },
      })
      .then((r) => r.data)
  },

  update(id: string, changes: AssetUpdate): Promise<Asset> {
    return client
      .patch<DataResponse<Asset>>(`/assets/${id}`, changes)
      .then((r) => r.data.data)
  },

  remove(id: string): Promise<void> {
    return client.delete(`/assets/${id}`).then(() => undefined)
  },
}
