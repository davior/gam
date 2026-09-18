import client from '@/api/client'

/** Transcripts, and the background jobs that produce them. */

export interface TranscriptWord {
  text: string
  start_time: number
  end_time: number
}

export interface TranscriptSegment {
  id: string
  idx: number
  text: string
  start_time: number
  end_time: number
  speaker: number | null
  edited: boolean
  words: TranscriptWord[]
}

export interface Transcript {
  asset_id: string
  status: 'running' | 'done' | 'error' | null
  model: string | null
  language: string | null
  segments: TranscriptSegment[]
}

export interface ActivityJob {
  id: string
  kind: string
  action: string
  status: 'queued' | 'processing' | 'done' | 'error' | 'cancelled'
  /** Marked active but no longer reporting; the sweeper will fail it shortly. */
  stalled: boolean
  stage: string
  progress: number
  detail: string
  asset_id: string | null
  asset_name: string
  model: string
  /** M7 only: set once a `extract_subvideo` "extract" job finishes, to the asset it
   *  created. `asset_id` stays pointed at the source throughout, so this is the only
   *  place to learn the result's id. */
  result_asset_id: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

interface DataResponse<T> {
  data: T
}

interface ListResponse<T> {
  data: T[]
  total: number
}

export const transcriptsApi = {
  start(assetId: string): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/assets/${assetId}/transcribe`)
      .then((r) => r.data.data)
  },

  get(assetId: string): Promise<Transcript> {
    return client
      .get<DataResponse<Transcript>>(`/assets/${assetId}/transcript`)
      .then((r) => r.data.data)
  },

  updateSegment(
    assetId: string,
    segmentId: string,
    changes: { text?: string; start_time?: number; end_time?: number }
  ): Promise<TranscriptSegment> {
    return client
      .patch<DataResponse<TranscriptSegment>>(
        `/assets/${assetId}/transcript/${segmentId}`,
        changes
      )
      .then((r) => r.data.data)
  },
}

export const activityApi = {
  list(
    params: { active?: boolean; asset_id?: string; limit?: number } = {}
  ): Promise<ActivityJob[]> {
    return client
      .get<ListResponse<ActivityJob>>('/activity', { params })
      .then((r) => r.data.data)
  },

  /** One job by id, for polling a specific run rather than the whole active list —
   *  M7's promote flow waits on several jobs at once, which the store's capped
   *  `jobs` array cannot be trusted to still contain all of by the time they finish. */
  get(kind: string, jobId: string): Promise<ActivityJob> {
    return client
      .get<DataResponse<ActivityJob>>(`/activity/${kind}/${jobId}`)
      .then((r) => r.data.data)
  },

  cancel(kind: string, jobId: string): Promise<ActivityJob> {
    return client
      .delete<DataResponse<ActivityJob>>(`/activity/${kind}/${jobId}`)
      .then((r) => r.data.data)
  },
}
