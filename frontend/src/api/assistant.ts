import client from '@/api/client'

/**
 * The assistant: saved conversations, turns that run in the background, and the proposal
 * a turn ends with. It never changes the library by itself: `apply` is the only call
 * that does, and it applies what `proposal` showed.
 */

interface DataResponse<T> {
  data: T
}

export interface AssistantStatus {
  /** False unless the app runs on Gecko Suite (AI_BACKEND=suite). */
  available: boolean
  /** Whether a turn could start now; `message` says what is missing when not. */
  ready: boolean
  model: string | null
  message: string | null
}

export interface AssistantPersona {
  id: string
  name: string
  description: string
  avatar: string | null
}

export interface AssistantSession {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface AssistantMessage {
  role: 'user' | 'assistant'
  text: string
  turn_id: string | null
  at: string
}

export interface AssistantStep {
  kind: 'think' | 'search' | 'read' | 'propose' | 'issue' | string
  text: string
}

/** One asset's part of a proposal. Only the fields that would change are set. */
export interface ProposalItem {
  asset_id: string
  asset_name: string
  /** What it is now, to show a change and not only the new value. */
  before: {
    name: string
    description: string | null
    summary: string | null
    tags: string[]
  }
  name?: string
  description?: string
  summary?: string
  add_tags: string[]
  remove_tags: string[]
  enrich: string[]
}

/**
 * A clip or a cut the assistant proposes from a video or audio asset. A `clip` is a live
 * window onto the original, made at once with no new file; a `cut` is a new video (or
 * audio) file, made by a background job after Apply.
 */
export interface ProposalClip {
  clip_id: string
  kind: 'clip' | 'cut'
  asset_id: string
  asset_name: string
  in_point: number
  out_point: number
  name: string | null
  /** Why this stretch, in the assistant's words. */
  reason: string | null
}

/** One moment (or whole asset) of a proposed playlist. Both points null: the whole asset. */
export interface ProposalPlaylistItem {
  asset_id: string
  asset_name: string
  asset_type: string
  in_point: number | null
  out_point: number | null
  label: string | null
  reason: string | null
}

/**
 * A playlist the assistant proposes: a new one (`playlist_id` null) or more items for one
 * the person has. Its moments are ranges kept in the playlist; no clips are made.
 */
export interface ProposalPlaylist {
  playlist_ref: string
  playlist_id: string | null
  name: string
  description?: string | null
  items: ProposalPlaylistItem[]
}

/**
 * A note for Gecko Notes, in Markdown. Written there, as the person, only on Apply: into
 * "From GAM", or `folder` when they named one. Its `/a/<id>` links become links to GAM.
 */
export interface ProposalNote {
  note_ref: string
  title: string
  markdown: string
  folder: string | null
  asset_ids: string[]
}

export interface Proposal {
  items: ProposalItem[]
  /** Absent on a turn from before clips could be proposed. */
  clips?: ProposalClip[]
  /** Absent on a turn from before playlists could be proposed. */
  playlists?: ProposalPlaylist[]
  /** Absent on a turn from before notes could be proposed. */
  notes?: ProposalNote[]
}

export type TurnStatus = 'queued' | 'processing' | 'done' | 'error' | 'cancelled'

export interface AssistantTurn {
  id: string
  session_id: string
  status: TurnStatus
  stage: string
  detail: string
  error: string | null
  prompt: string
  focus_asset_id: string | null
  /** The suite persona this turn was asked as, if any. */
  persona_id: string | null
  steps: AssistantStep[]
  reply: string
  proposal: Proposal | null
  /** pending | applied | discarded once there is a proposal; '' otherwise. */
  outcome: '' | 'pending' | 'applied' | 'discarded'
  cost: number | null
  created_at: string
}

export interface AssistantSessionDetail extends AssistantSession {
  messages: AssistantMessage[]
  turns: AssistantTurn[]
}

export interface ApplyResult {
  /** Null on a playlist's result, which is about no one asset. */
  asset_id: string | null
  asset_name: string
  applied: string[]
  skipped: string[]
  /** Set on the result of a clip or a cut, whose `asset_id` is then its source. */
  clip_id?: string | null
  /** The clip that was made. A cut has none yet: a job makes it. */
  created_asset_id?: string | null
  /** Set on the result of a playlist: which proposed one, and the playlist it made or
   *  added to. */
  playlist_ref?: string | null
  playlist_id?: string | null
  /** Set on the result of a note: which proposed one, and its page in Gecko Notes. */
  note_ref?: string | null
  note_url?: string | null
}

/**
 * What to apply. No list: everything. Any: each restricts its own kind, and a kind left
 * out is not applied.
 */
export interface ApplySelection {
  assetIds?: string[]
  clipIds?: string[]
  playlistRefs?: string[]
  noteRefs?: string[]
}

export function isActiveTurn(turn: Pick<AssistantTurn, 'status'>): boolean {
  return turn.status === 'queued' || turn.status === 'processing'
}

export const assistantApi = {
  status(): Promise<AssistantStatus> {
    return client
      .get<DataResponse<AssistantStatus>>('/assistant/status')
      .then((r) => r.data.data)
  },

  listSessions(): Promise<AssistantSession[]> {
    return client
      .get<DataResponse<AssistantSession[]>>('/assistant/sessions', {
        params: { limit: 50 },
      })
      .then((r) => r.data.data)
  },

  createSession(): Promise<AssistantSession> {
    return client
      .post<DataResponse<AssistantSession>>('/assistant/sessions')
      .then((r) => r.data.data)
  },

  getSession(id: string): Promise<AssistantSessionDetail> {
    return client
      .get<DataResponse<AssistantSessionDetail>>(`/assistant/sessions/${id}`)
      .then((r) => r.data.data)
  },

  deleteSession(id: string): Promise<void> {
    return client.delete(`/assistant/sessions/${id}`).then(() => undefined)
  },

  /** The personas the person may ask as (the suite's). Empty when there are none. */
  personas(): Promise<AssistantPersona[]> {
    return client
      .get<DataResponse<AssistantPersona[]>>('/assistant/personas')
      .then((r) => r.data.data)
  },

  ask(
    sessionId: string,
    prompt: string,
    focusAssetId?: string | null,
    personaId?: string | null
  ): Promise<AssistantTurn> {
    return client
      .post<DataResponse<AssistantTurn>>(`/assistant/sessions/${sessionId}/turns`, {
        prompt,
        focus_asset_id: focusAssetId ?? null,
        persona_id: personaId ?? null,
      })
      .then((r) => r.data.data)
  },

  getTurn(id: string): Promise<AssistantTurn> {
    return client
      .get<DataResponse<AssistantTurn>>(`/assistant/turns/${id}`)
      .then((r) => r.data.data)
  },

  cancel(id: string): Promise<AssistantTurn> {
    return client
      .post<DataResponse<AssistantTurn>>(`/assistant/turns/${id}/cancel`)
      .then((r) => r.data.data)
  },

  apply(id: string, selection?: ApplySelection): Promise<ApplyResult[]> {
    return client
      .post<DataResponse<ApplyResult[]>>(`/assistant/turns/${id}/apply`, {
        asset_ids: selection?.assetIds ?? null,
        clip_ids: selection?.clipIds ?? null,
        playlist_refs: selection?.playlistRefs ?? null,
        note_refs: selection?.noteRefs ?? null,
      })
      .then((r) => r.data.data)
  },

  discard(id: string): Promise<AssistantTurn> {
    return client
      .post<DataResponse<AssistantTurn>>(`/assistant/turns/${id}/discard`)
      .then((r) => r.data.data)
  },
}
