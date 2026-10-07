import { create } from 'zustand'
import {
  assetsApi,
  type Asset,
  type AssetType,
  type AssetUpdate,
  type ListAssetsParams,
  type UploadRejection,
} from '@/api/assets'
import { clipsApi } from '@/api/clips'
import { tagsApi, type Tag } from '@/api/tags'
import { useTagStore } from '@/stores/tags'
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

/**
 * The server refuses a bulk call over `MAX_BULK_ASSETS` (`schemas_tags.py`), and the
 * grid can hold more than that once a few pages are loaded, so a large selection is
 * chunked rather than sent whole and rejected.
 */
const BULK_CHUNK = 500

interface LibraryState {
  assets: Asset[]
  total: number
  loading: boolean
  loadingMore: boolean
  error: string | null

  query: string
  typeFilter: AssetType | null
  /** Tag *names*, ANDed by the server. Names rather than ids so a filter survives a rename. */
  tagFilter: string[]
  categoryFilter: string | null
  sourceFilter: string | null
  minDuration: number | null
  maxDuration: number | null
  /** ISO dates (`YYYY-MM-DD`) straight from `<input type="date">`. */
  uploadedAfter: string | null
  uploadedBefore: string | null
  /** M10. Substring matches, resolved through a clip's parent server-side, so filtering
   *  by publisher returns the clips that inherit it too. */
  creatorFilter: string | null
  publisherFilter: string | null
  /** True for "still missing a source", which is how a backlog gets worked through. */
  unattributedOnly: boolean

  uploading: boolean
  uploadProgress: number
  rejections: UploadRejection[]

  load: () => Promise<void>
  loadMore: () => Promise<void>
  setQuery: (query: string) => void
  setTypeFilter: (type: AssetType | null) => void
  toggleTag: (name: string) => void
  setCategoryFilter: (categoryId: string | null) => void
  setSourceFilter: (source: string | null) => void
  setDurationRange: (min: number | null, max: number | null) => void
  setUploadedRange: (after: string | null, before: string | null) => void
  setCreatorFilter: (creator: string | null) => void
  setPublisherFilter: (publisher: string | null) => void
  setUnattributedOnly: (only: boolean) => void
  clearFilters: () => void
  upload: (files: File[]) => Promise<void>
  update: (id: string, changes: AssetUpdate) => Promise<void>
  remove: (id: string) => Promise<void>
  /** Delete an asset together with the live clips cut from it — the delete guard's
   *  "Delete clips too". `clipIds` are the clips the guard listed, dropped from the
   *  grid alongside it. */
  removeWithClips: (id: string, clipIds: string[]) => Promise<void>
  /** Put an asset made somewhere other than the grid — a clip saved from the Clip
   *  tab — at the top of it. */
  prepend: (asset: Asset) => void
  /** Replace one asset's tags with the authoritative set the server just returned. */
  setAssetTags: (assetId: string, tags: Tag[]) => void
  /** Re-read one asset from the server, for when something other than the user
   *  changed it — an enrichment job writing a summary, for instance. */
  refreshAsset: (id: string) => Promise<void>
  openById: (id: string) => Promise<Asset>
  /** Put a finished URL import — and the chapter clips it made — at the top of the grid.
   *  Also each output of a finished generation, which has no clips but loses nothing by
   *  the check. */
  addImported: (id: string) => Promise<void>
  applyTags: (assetIds: string[], add: string[], remove: string[]) => Promise<void>
  dismissRejections: () => void
  reset: () => void
}

/** Bumped on every filter change; a response tagged with an older value is discarded. */
let requestToken = 0

/** Everything cleared by `reset()` and by "clear all" — one list, so none is forgotten. */
const NO_FILTERS = {
  query: '',
  typeFilter: null,
  tagFilter: [] as string[],
  categoryFilter: null,
  sourceFilter: null,
  minDuration: null,
  maxDuration: null,
  uploadedAfter: null,
  uploadedBefore: null,
  creatorFilter: null,
  publisherFilter: null,
  unattributedOnly: false,
} satisfies Partial<LibraryState>

/**
 * The filters as query parameters.
 *
 * Built in one place because `load` and `loadMore` must send exactly the same set —
 * when they each assembled their own, a filter added to one and missed in the other
 * would apply to page one and quietly stop applying to page two, which reads as the
 * grid inventing rows.
 */
function filterParams(state: LibraryState): ListAssetsParams {
  return {
    q: state.query.trim() || undefined,
    asset_type: state.typeFilter ?? undefined,
    tag: state.tagFilter.length > 0 ? state.tagFilter : undefined,
    category_id: state.categoryFilter ?? undefined,
    source: state.sourceFilter ?? undefined,
    min_duration: state.minDuration ?? undefined,
    max_duration: state.maxDuration ?? undefined,
    uploaded_after: state.uploadedAfter ?? undefined,
    uploaded_before: state.uploadedBefore ?? undefined,
    creator: state.creatorFilter ?? undefined,
    publisher: state.publisherFilter ?? undefined,
    // Only sent when on: `false` is a real filter server-side ("everything that *has*
    // attribution"), and sending it whenever the chip is off would hide every
    // unattributed asset from the default library view.
    unattributed: state.unattributedOnly ? true : undefined,
  }
}

export const useLibraryStore = create<LibraryState>((set, get) => ({
  assets: [],
  total: 0,
  loading: false,
  loadingMore: false,
  error: null,

  ...NO_FILTERS,

  uploading: false,
  uploadProgress: 0,
  rejections: [],

  async load() {
    const token = ++requestToken
    set({ loading: true, error: null })

    try {
      const page = await assetsApi.list({ ...filterParams(get()), limit: PAGE_SIZE })
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
    const state = get()
    const { assets, total, loadingMore, loading } = state
    if (loadingMore || loading || assets.length >= total) return

    const token = requestToken
    set({ loadingMore: true })

    try {
      const page = await assetsApi.list({
        ...filterParams(state),
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

  toggleTag(name) {
    // Compared case-insensitively because the server matches tags that way; otherwise
    // clicking the same chip as "NATO" and as "nato" would stack two filters that ANDed
    // to the same set, and neither ✕ would clear it.
    const lowered = name.toLowerCase()
    const current = get().tagFilter
    const next = current.some((t) => t.toLowerCase() === lowered)
      ? current.filter((t) => t.toLowerCase() !== lowered)
      : [...current, name]
    set({ tagFilter: next })
    void get().load()
  },

  setCategoryFilter(categoryFilter) {
    set({ categoryFilter })
    void get().load()
  },

  setSourceFilter(sourceFilter) {
    set({ sourceFilter })
    void get().load()
  },

  setDurationRange(minDuration, maxDuration) {
    set({ minDuration, maxDuration })
    void get().load()
  },

  setCreatorFilter(creatorFilter) {
    set({ creatorFilter })
    void get().load()
  },

  setPublisherFilter(publisherFilter) {
    set({ publisherFilter })
    void get().load()
  },

  setUnattributedOnly(unattributedOnly) {
    set({ unattributedOnly })
    void get().load()
  },

  setUploadedRange(uploadedAfter, uploadedBefore) {
    set({ uploadedAfter, uploadedBefore })
    void get().load()
  },

  clearFilters() {
    set({ ...NO_FILTERS })
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
    const { assets: previous, total: previousTotal } = get()
    // Only counted down when the row was actually loaded. A clip deleted from the Clip
    // tab need not be — it may be filtered out, or on a page not fetched yet — and
    // taking one off the count anyway would make the total drift.
    const present = previous.some((a) => a.id === id)
    set((state) => ({
      assets: state.assets.filter((a) => a.id !== id),
      total: present ? Math.max(0, state.total - 1) : state.total,
    }))

    try {
      await assetsApi.remove(id)
    } catch (error) {
      // The server's total, not `previous.length`: that is only the rows loaded so
      // far, and after a few pages of scrolling the count would drop to a page's worth.
      set({ assets: previous, total: previousTotal })
      set({ error: apiErrorMessage(error, 'Could not delete that asset') })
      throw error
    }
  },

  async removeWithClips(id, clipIds) {
    // Waits for the server, unlike `remove`. Dropping the parent first would unmount
    // AssetView's panel (it renders from `assets.find`) and mount a fresh one when a
    // failure put it back, discarding the guard's own error state — the remount bug
    // docs/m7-clips-and-subvideos.md records. No store `error` either: the guard
    // shows its message in place, and staying mounted is what lets it.
    //
    // No staleness token: filtering out ids the server just deleted is right whatever
    // loaded in the meantime, and after a sign-out there is nothing left to filter.
    await assetsApi.remove(id, { withClips: true })
    const gone = new Set([id, ...clipIds])
    set((state) => {
      const assets = state.assets.filter((a) => !gone.has(a.id))
      return {
        assets,
        total: Math.max(0, state.total - (state.assets.length - assets.length)),
      }
    })
  },

  prepend(asset) {
    // At the top regardless of the active filters, the same rule `upload` and
    // `addImported` follow: it is what the user just made, and seeing it where they are
    // looking is the confirmation.
    set((state) =>
      state.assets.some((a) => a.id === asset.id)
        ? state
        : { assets: [asset, ...state.assets], total: state.total + 1 }
    )
  },

  async openById(id) {
    // Fetches one asset *and puts it in `assets`*, which is the whole point. `update`,
    // `remove`, `setAssetTags` and `refreshAsset` all work by mapping over that list, so
    // a detail view rendered for an asset that is not in it would show working controls
    // whose every write silently landed nowhere.
    //
    // No staleness token: this is keyed to a route parameter, so a second call means the
    // user navigated to a different asset and its result is the one that should win.
    const fresh = await assetsApi.get(id)
    set((state) => ({
      assets: state.assets.some((a) => a.id === id)
        ? state.assets.map((a) => (a.id === id ? fresh : a))
        : [fresh, ...state.assets],
    }))
    return fresh
  },

  async addImported(id) {
    // Prepended regardless of the active filters, the same as `upload`: this is what the
    // user just asked for, and it appearing where they are looking is the confirmation.
    //
    // Its chapter clips are fetched too. They are assets in their own right and the
    // server lists them, so leaving them out would make the grid disagree with the next
    // reload. Newest first, as the server orders them: the clips were cut after the
    // parent existed, so they sit above it.
    //
    // Guarded like `load`: a filter change or a sign-out in the meantime supersedes this,
    // and the load that follows either includes it or correctly does not.
    const token = requestToken
    try {
      const [asset, children] = await Promise.all([assetsApi.get(id), clipsApi.list(id)])
      if (token !== requestToken) return
      const clips = children.filter((c) => c.source === 'clip')
      set((state) => {
        const present = new Set(state.assets.map((a) => a.id))
        const fresh = [...clips, asset].filter((a) => !present.has(a.id))
        return { assets: [...fresh, ...state.assets], total: state.total + fresh.length }
      })
    } catch {
      /* the next load will pick it up */
    }
  },

  async refreshAsset(id) {
    // No optimistic step and no error surfaced: this runs after a background job, not
    // after something the user did, so a failure here should leave the panel showing
    // what it already had rather than interrupting them with a message about a
    // request they never made.
    try {
      const fresh = await assetsApi.get(id)
      set((state) => ({
        assets: state.assets.map((a) => (a.id === id ? fresh : a)),
      }))
    } catch {
      /* the next load will pick it up */
    }
  },

  setAssetTags(assetId, tags) {
    set((state) => ({
      assets: state.assets.map((a) => (a.id === assetId ? { ...a, tags } : a)),
    }))
  },

  async applyTags(assetIds, add, remove) {
    if (assetIds.length === 0 || (add.length === 0 && remove.length === 0)) return

    const previous = get().assets
    try {
      const added: Tag[] = []
      for (let i = 0; i < assetIds.length; i += BULK_CHUNK) {
        const result = await tagsApi.bulk(assetIds.slice(i, i + BULK_CHUNK), add, remove)
        added.push(...result.tags_added)
      }

      // Patch locally rather than refetch: the server's answer is fully determined —
      // `tags_added` is every tag the names resolved to, attaching is idempotent, and
      // adds are applied before removes, so the same order here gives the same result.
      const removed = new Set(remove)
      const touched = new Set(assetIds)
      set((state) => ({
        assets: state.assets.map((asset) => {
          if (!touched.has(asset.id)) return asset
          const merged = new Map(asset.tags.map((t) => [t.id, t]))
          added.forEach((tag) => merged.set(tag.id, tag))
          removed.forEach((id) => merged.delete(id))
          return {
            ...asset,
            tags: [...merged.values()].sort((a, b) => a.name.localeCompare(b.name)),
          }
        }),
      }))

      // Autocomplete should know about a tag this call just created.
      useTagStore.getState().remember(added)

      // A local patch cannot know whether an asset still belongs in a filtered list —
      // removing the very tag being filtered on takes it out of the set. Reload rather
      // than show rows that no longer match. This does cost the loaded pages; being
      // wrong about what is on screen is worse than losing scroll position.
      const { tagFilter, categoryFilter } = get()
      if (tagFilter.length > 0 || categoryFilter) void get().load()
    } catch (error) {
      set({
        assets: previous,
        error: apiErrorMessage(error, 'Could not apply those tags'),
      })
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
      ...NO_FILTERS,
      uploading: false,
      uploadProgress: 0,
      rejections: [],
    })
  },
}))
