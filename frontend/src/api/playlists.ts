import client from '@/api/client'

/**
 * Playlists: named, ordered runs of assets and moments of them.
 *
 * An item is an asset plus an optional range on its own file, never a clip: a live clip
 * added to a playlist is stored as its original and its window, so `asset_id` always
 * names something with a file to play (and that Video Creator can cut from).
 */

export interface PlaylistItem {
  id: string
  position: number
  asset_id: string
  asset_name: string
  asset_type: 'video' | 'audio' | 'image' | string
  /** As stored. Both null: the whole asset. */
  in_point: number | null
  out_point: number | null
  /** Resolved: where playback starts and stops. `end` is null only when the asset's
   *  length was never measured; play to the file's own end then. */
  start: number
  end: number | null
  /** Images only; null means the default. */
  image_seconds: number | null
  label: string | null
  note: string | null
  asset_duration_seconds: number | null
  mime_type: string | null
  width: number | null
  height: number | null
  /** Signed and time-limited: read the playlist again for fresh ones. */
  file_url: string | null
  thumb_url: string | null
  missing: boolean
}

export interface Playlist {
  id: string
  name: string
  description: string | null
  item_count: number
  total_seconds: number
  thumb_url: string | null
  created_at: string
  updated_at: string
}

export interface PlaylistDetail extends Playlist {
  items: PlaylistItem[]
}

export interface PlaylistItemIn {
  asset_id: string
  in_point?: number | null
  out_point?: number | null
  image_seconds?: number | null
  label?: string | null
  note?: string | null
}

/** Only the keys present are changed. A range is sent as a pair; both null = whole asset. */
export type PlaylistItemChanges = Partial<
  Pick<PlaylistItem, 'in_point' | 'out_point' | 'image_seconds' | 'label' | 'note'>
>

/** How long an image shows when its item does not say (services/playlists.py). */
export const IMAGE_SECONDS = 5

interface ListResponse<T> {
  data: T[]
  total: number
}

interface DataResponse<T> {
  data: T
}

export const playlistsApi = {
  list(): Promise<Playlist[]> {
    return client.get<ListResponse<Playlist>>('/playlists').then((r) => r.data.data)
  },

  get(id: string): Promise<PlaylistDetail> {
    return client
      .get<DataResponse<PlaylistDetail>>(`/playlists/${id}`)
      .then((r) => r.data.data)
  },

  create(
    name: string,
    items: PlaylistItemIn[] = [],
    description?: string | null
  ): Promise<PlaylistDetail> {
    return client
      .post<DataResponse<PlaylistDetail>>('/playlists', {
        name,
        description: description ?? null,
        items,
      })
      .then((r) => r.data.data)
  },

  update(
    id: string,
    changes: { name?: string; description?: string | null }
  ): Promise<PlaylistDetail> {
    return client
      .patch<DataResponse<PlaylistDetail>>(`/playlists/${id}`, changes)
      .then((r) => r.data.data)
  },

  remove(id: string): Promise<void> {
    return client.delete(`/playlists/${id}`).then(() => undefined)
  },

  addItems(
    id: string,
    items: PlaylistItemIn[],
    position?: number
  ): Promise<PlaylistDetail> {
    return client
      .post<DataResponse<PlaylistDetail>>(`/playlists/${id}/items`, {
        items,
        position: position ?? null,
      })
      .then((r) => r.data.data)
  },

  updateItem(
    id: string,
    itemId: string,
    changes: PlaylistItemChanges
  ): Promise<PlaylistDetail> {
    return client
      .patch<DataResponse<PlaylistDetail>>(`/playlists/${id}/items/${itemId}`, changes)
      .then((r) => r.data.data)
  },

  removeItem(id: string, itemId: string): Promise<PlaylistDetail> {
    return client
      .delete<DataResponse<PlaylistDetail>>(`/playlists/${id}/items/${itemId}`)
      .then((r) => r.data.data)
  },

  reorder(id: string, itemIds: string[]): Promise<PlaylistDetail> {
    return client
      .put<DataResponse<PlaylistDetail>>(`/playlists/${id}/order`, { item_ids: itemIds })
      .then((r) => r.data.data)
  },
}
