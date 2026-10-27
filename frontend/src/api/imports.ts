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

/** One track a music library's page lists, as the preview found it. */
export interface PageTrack {
  audio_url: string
  title: string
  creator: string | null
  publisher: string | null
  source_title: string | null
  description: string | null
  duration_seconds: number | null
  published_date: string | null
  license: string | null
  credit_line: string | null
  source_url: string | null
  genres: string[]
  moods: string[]
  instruments: string[]
  tags: string[]
  bpm: number | null
  /** Set when the library already holds this track. */
  existing_asset_id?: string | null
  /** Set when an audio file with the same title (and artist) is already in the library:
   *  probably the same piece from elsewhere. A warning; it can still be imported. */
  similar_asset_id?: string | null
}

export interface PagePreview {
  page_url: string
  /** How the tracks were found, and so how far their details can be trusted:
   *  `structured` is the site's own data, `ai` a model's reading of the page, `links`
   *  the bare audio links. */
  method: 'structured' | 'ai' | 'links'
  /** The adapter or source: "incompetech", "mixkit", "schema.org", "AI", "links". */
  source: string
  note: string
  tracks: PageTrack[]
}

/** A track read from a page the person saved. It may have no audio URL (Pixabay's
 *  download is a button); `match_id` is the site's id, which its downloaded filenames
 *  carry. */
export type SavedPageTrack = Omit<PageTrack, 'audio_url'> & {
  audio_url: string | null
  match_id: string | null
}

export type SavedPagePreview = Omit<PagePreview, 'tracks'> & { tracks: SavedPageTrack[] }

export interface SavedFileResult {
  filename: string
  status: 'imported' | 'duplicate' | 'unmatched' | 'rejected'
  asset_id: string | null
  track_title: string | null
  message: string
}

export interface SavedImport {
  page_url: string
  source: string
  results: SavedFileResult[]
}

/** The most tracks one import may queue; the server refuses more. */
export const MAX_TRACKS_PER_IMPORT = 50

interface DataResponse<T> {
  data: T
}

function multipart(page: File, files: File[] = []): FormData {
  const body = new FormData()
  body.append('page', page)
  files.forEach((file) => body.append('files', file))
  return body
}

// Content-Type left to the browser, which must add the multipart boundary itself.
const MULTIPART = { headers: { 'Content-Type': undefined } }

export const importsApi = {
  /** Read a page the person saved in their browser. Uploads only the page. */
  previewSavedPage(page: File): Promise<SavedPagePreview> {
    return client
      .post<DataResponse<SavedPagePreview>>(
        '/assets/import-page/saved/preview',
        multipart(page),
        MULTIPART
      )
      .then((r) => r.data.data)
  },

  /** Import downloaded tracks with the details their saved page gives. */
  importSavedPage(page: File, files: File[]): Promise<SavedImport> {
    return client
      .post<DataResponse<SavedImport>>(
        '/assets/import-page/saved',
        multipart(page, files),
        MULTIPART
      )
      .then((r) => r.data.data)
  },

  /** Read a music library's page and list its tracks. Downloads nothing. */
  previewPage(url: string, useAi = true): Promise<PagePreview> {
    return client
      .post<DataResponse<PagePreview>>('/assets/import-page/preview', {
        url,
        use_ai: useAi,
      })
      .then((r) => r.data.data)
  },

  /** Queue one download per chosen track. Returns the jobs, one per track. */
  importTracks(tracks: PageTrack[]): Promise<ActivityJob[]> {
    return client
      .post<DataResponse<ActivityJob[]>>('/assets/import-page', { tracks })
      .then((r) => r.data.data)
  },

  fromUrl(request: UrlImportRequest): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>('/assets/import', request)
      .then((r) => r.data.data)
  },
}
