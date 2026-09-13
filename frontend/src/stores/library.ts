import { create } from 'zustand'
import {
  assetsApi,
  type Asset,
  type AssetType,
  type AssetUpdate,
  type UploadRejection,
} from '@/api/assets'
import { apiErrorMessage } from '@/api/client'

/**
 * The library listing.
 *
 * Every await is guarded against a stale response, the pattern carried over from
 * gecko-notes' assets store: typing in the filter fires several requests, and without
 * the guard a slow early one can land after a fast later one and overwrite the results
 * the user is looking at.
 */

const PAGE_SIZE = 60

interface LibraryState {
  assets: Asset[]
  total: number
  loading: boolean
  loadingMore: boolean
  error: string | null

  query: string
  typeFilter: AssetType | null

  uploading: boolean
  uploadProgress: number
  rejections: UploadRejection[]

  load: () => Promise<void>
  loadMore: () => Promise<void>
  setQuery: (query: string) => void
  setTypeFilter: (type: AssetType | null) => void
  upload: (files: File[]) => Promise<void>
  update: (id: string, changes: AssetUpdate) => Promise<void>
  remove: (id: string) => Promise<void>
  dismissRejections: () => void
  reset: () => void
}

/** Bumped on every filter change; a response tagged with an older value is discarded. */
let requestToken = 0

export const useLibraryStore = create<LibraryState>((set, get) => ({
  assets: [],
  total: 0,
  loading: false,
  loadingMore: false,
  error: null,

  query: '',
  typeFilter: null,

  uploading: false,
  uploadProgress: 0,
  rejections: [],

  async load() {
    const token = ++requestToken
    set({ loading: true, error: null })

    try {
      const { query, typeFilter } = get()
      const page = await assetsApi.list({
        q: query.trim() || undefined,
        asset_type: typeFilter ?? undefined,
        limit: PAGE_SIZE,
      })
      if (token !== requestToken) return
      set({ assets: page.data, total: page.total, loading: false })
    } catch (error) {
      if (token !== requestToken) return
      set({
        loading: false,
        error: apiErrorMessage(error, 'Could not load your library'),
      })
    }
  },

  async loadMore() {
    const { assets, total, loadingMore, loading, query, typeFilter } = get()
    if (loadingMore || loading || assets.length >= total) return

    const token = requestToken
    set({ loadingMore: true })

    try {
      const page = await assetsApi.list({
        q: query.trim() || undefined,
        asset_type: typeFilter ?? undefined,
        limit: PAGE_SIZE,
        offset: assets.length,
      })
      if (token !== requestToken) return
      // Appending by id rather than index: an upload can land between pages, and
      // offset paging would otherwise show the boundary asset twice.
      const seen = new Set(get().assets.map((a) => a.id))
      const fresh = page.data.filter((a) => !seen.has(a.id))
      set({ assets: [...get().assets, ...fresh], total: page.total, loadingMore: false })
    } catch (error) {
      if (token !== requestToken) return
      set({ loadingMore: false, error: apiErrorMessage(error, 'Could not load more') })
    }
  },

  setQuery(query) {
    set({ query })
    void get().load()
  },

  setTypeFilter(typeFilter) {
    set({ typeFilter })
    void get().load()
  },

  async upload(files) {
    if (files.length === 0) return
    set({ uploading: true, uploadProgress: 0, error: null, rejections: [] })

    try {
      const result = await assetsApi.upload(files, (fraction) => {
        set({ uploadProgress: fraction })
      })
      // Newest first, matching the server's ordering, so an upload appears where the
      // user is already looking rather than at the bottom of the grid.
      set((state) => ({
        assets: [...result.created.reverse(), ...state.assets],
        total: state.total + result.created.length,
        rejections: result.rejected,
        uploading: false,
        uploadProgress: 0,
      }))
    } catch (error) {
      set({
        uploading: false,
        uploadProgress: 0,
        error: apiErrorMessage(error, 'Upload failed'),
      })
    }
  },

  async update(id, changes) {
    // Optimistic: renaming should feel instant. On failure the server's version is
    // put back, so the grid never keeps an edit that did not persist.
    const previous = get().assets.find((a) => a.id === id)
    set((state) => ({
      assets: state.assets.map((a) => (a.id === id ? { ...a, ...changes } : a)),
    }))

    try {
      const saved = await assetsApi.update(id, changes)
      set((state) => ({ assets: state.assets.map((a) => (a.id === id ? saved : a)) }))
    } catch (error) {
      if (previous) {
        set((state) => ({
          assets: state.assets.map((a) => (a.id === id ? previous : a)),
        }))
      }
      set({ error: apiErrorMessage(error, 'Could not save your changes') })
      throw error
    }
  },

  async remove(id) {
    const previous = get().assets
    set((state) => ({
      assets: state.assets.filter((a) => a.id !== id),
      total: Math.max(0, state.total - 1),
    }))

    try {
      await assetsApi.remove(id)
    } catch (error) {
      set({ assets: previous, total: previous.length })
      set({ error: apiErrorMessage(error, 'Could not delete that asset') })
      throw error
    }
  },

  dismissRejections() {
    set({ rejections: [] })
  },

  reset() {
    requestToken += 1
    set({
      assets: [],
      total: 0,
      loading: false,
      loadingMore: false,
      error: null,
      query: '',
      typeFilter: null,
      uploading: false,
      uploadProgress: 0,
      rejections: [],
    })
  },
}))
