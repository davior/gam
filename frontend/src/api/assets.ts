import client from '@/api/client'
import type { Tag } from '@/api/tags'
import type { ActivityJob } from '@/api/transcripts'

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

  /**
   * M10. Whose work this is — as opposed to `source`, which is how the file got here.
   *
   * These are the *resolved* values: a clip with nothing of its own carries what it
   * inherited from its parent, so a clip of an attributed video shows a real credit
   * rather than eight blanks. `attribution_inherited` names which of them came that
   * way, so the panel can mark them instead of letting an inherited value look like
   * something typed on the clip.
   */
  source_url: string | null
  creator: string | null
  publisher: string | null
  source_title: string | null
  /** ISO 8601 partial: `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. Partial on purpose — a book
   *  is from 1994 and nothing should invent a January 1st for it. */
  published_date: string | null
  retrieved_at: string | null
  license: string | null
  /** An override. Null means the displayed `credit` is composed from the fields above. */
  credit_line: string | null

  /** Read-only: the line to display, composed unless `credit_line` overrides it. */
  credit: string
  attribution_inherited: string[]

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
  /** M10. Each of these resolves through a clip's parent server-side, so filtering by
   *  publisher returns the clips that inherit it as well as the asset itself. */
  creator?: string
  publisher?: string
  source_title?: string
  published_after?: string
  published_before?: string
  /** True for "still missing a source" — how a backlog gets worked through. */
  unattributed?: boolean
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

  /** M10. The only path that can *correct* attribution: the harvester fills blanks
   *  only, and an AI may propose but never write. */
  source_url?: string | null
  creator?: string | null
  publisher?: string | null
  source_title?: string | null
  published_date?: string | null
  retrieved_at?: string | null
  license?: string | null
  credit_line?: string | null
}

/** The attribution fields, in the order the panel shows them. */
export const ATTRIBUTION_FIELDS = [
  'creator',
  'source_title',
  'publisher',
  'published_date',
  'source_url',
  'license',
  'retrieved_at',
  'credit_line',
] as const

export type AttributionField = (typeof ATTRIBUTION_FIELDS)[number]

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

  /**
   * Re-read embedded metadata for every file already in the library.
   *
   * For everything uploaded before M10 existed: the EXIF and ID3 have been sitting on
   * disk the whole time and nothing ever looked. Returns the queued job, which shows up
   * in the activity feed like any other.
   */
  harvestAttribution(): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>('/assets/harvest-attribution')
      .then((r) => r.data.data)
  },
}
