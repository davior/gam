import { create } from 'zustand'
import {
  playlistsApi,
  type Playlist,
  type PlaylistDetail,
  type PlaylistItemChanges,
  type PlaylistItemIn,
} from '@/api/playlists'
import { apiErrorMessage } from '@/api/client'

/**
 * The person's playlists, and the one open on the playlist page.
 *
 * Every write answers with the whole playlist, so `current` is replaced from the server
 * rather than patched here: the server resolves ranges and signs URLs, and a local patch
 * would show a range it might have refused.
 *
 * Two guards against stale responses, the pattern from `stores/tags.ts`: one for the
 * list, and an epoch for `current` that changes when another playlist is opened, so a
 * slow save to the last one cannot land on this one.
 */

interface PlaylistState {
  playlists: Playlist[]
  loaded: boolean
  loading: boolean
  error: string | null

  current: PlaylistDetail | null
  currentError: string | null

  load: () => Promise<void>
  ensureLoaded: () => void
  /** Make a playlist, optionally with items, and add it to the list. Resolves to it. */
  create: (name: string, items?: PlaylistItemIn[]) => Promise<PlaylistDetail>
  /** Add items to a playlist from anywhere (the library, an asset page). */
  addTo: (id: string, items: PlaylistItemIn[]) => Promise<PlaylistDetail>
  remove: (id: string) => Promise<void>

  open: (id: string) => Promise<void>
  /** Read the open playlist again, for fresh signed URLs. */
  refresh: () => Promise<void>
  rename: (changes: { name?: string; description?: string | null }) => Promise<void>
  updateItem: (itemId: string, changes: PlaylistItemChanges) => Promise<void>
  removeItem: (itemId: string) => Promise<void>
  /** Move one item up (-1) or down (+1). */
  move: (itemId: string, delta: -1 | 1) => Promise<void>
  reset: () => void
}

let listToken = 0
let epoch = 0

export const usePlaylistStore = create<PlaylistState>((set, get) => {
  /** Put a playlist the server just returned into the list as well, so the list page and
   *  the "add to playlist" picker never show a count from before an edit. */
  function remember(detail: PlaylistDetail) {
    const summary: Playlist = {
      id: detail.id,
      name: detail.name,
      description: detail.description,
      item_count: detail.item_count,
      total_seconds: detail.total_seconds,
      thumb_url: detail.thumb_url,
      created_at: detail.created_at,
      updated_at: detail.updated_at,
    }
    const rest = get().playlists.filter((p) => p.id !== detail.id)
    set({ playlists: [summary, ...rest] })
  }

  /** Run a write against the open playlist and take the server's answer. */
  async function write(run: (id: string) => Promise<PlaylistDetail>, fallback: string) {
    const current = get().current
    if (!current) return
    const mine = epoch
    try {
      const detail = await run(current.id)
      if (mine !== epoch) return
      set({ current: detail, currentError: null })
      remember(detail)
    } catch (error) {
      if (mine !== epoch) return
      set({ currentError: apiErrorMessage(error, fallback) })
      throw error
    }
  }

  return {
    playlists: [],
    loaded: false,
    loading: false,
    error: null,
    current: null,
    currentError: null,

    async load() {
      const token = ++listToken
      set({ loading: true, error: null })
      try {
        const playlists = await playlistsApi.list()
        if (token !== listToken) return
        set({ playlists, loaded: true, loading: false })
      } catch (error) {
        if (token !== listToken) return
        set({
          loading: false,
          error: apiErrorMessage(error, 'Could not load the playlists'),
        })
      }
    },

    ensureLoaded() {
      const { loaded, loading } = get()
      if (!loaded && !loading) void get().load()
    },

    async create(name, items = []) {
      const detail = await playlistsApi.create(name, items)
      remember(detail)
      return detail
    },

    async addTo(id, items) {
      const detail = await playlistsApi.addItems(id, items)
      remember(detail)
      if (get().current?.id === id) set({ current: detail })
      return detail
    },

    async remove(id) {
      await playlistsApi.remove(id)
      set({ playlists: get().playlists.filter((p) => p.id !== id) })
      if (get().current?.id === id) {
        epoch++
        set({ current: null })
      }
    },

    async open(id) {
      const mine = ++epoch
      if (get().current?.id !== id) set({ current: null })
      set({ currentError: null })
      try {
        const detail = await playlistsApi.get(id)
        if (mine !== epoch) return
        set({ current: detail })
      } catch (error) {
        if (mine !== epoch) return
        set({ currentError: apiErrorMessage(error, 'Could not open the playlist') })
      }
    },

    async refresh() {
      await write((id) => playlistsApi.get(id), 'Could not reload the playlist').catch(
        () => undefined
      )
    },

    rename(changes) {
      return write(
        (id) => playlistsApi.update(id, changes),
        'Could not rename the playlist'
      )
    },

    updateItem(itemId, changes) {
      return write(
        (id) => playlistsApi.updateItem(id, itemId, changes),
        'Could not change that item'
      )
    },

    removeItem(itemId) {
      return write(
        (id) => playlistsApi.removeItem(id, itemId),
        'Could not remove that item'
      )
    },

    move(itemId, delta) {
      const items = get().current?.items ?? []
      const ids = items.map((i) => i.id)
      const at = ids.indexOf(itemId)
      const to = at + delta
      if (at < 0 || to < 0 || to >= ids.length) return Promise.resolve()
      ;[ids[at], ids[to]] = [ids[to], ids[at]]
      return write((id) => playlistsApi.reorder(id, ids), 'Could not move that item')
    },

    reset() {
      listToken++
      epoch++
      set({
        playlists: [],
        loaded: false,
        loading: false,
        error: null,
        current: null,
        currentError: null,
      })
    },
  }
})
