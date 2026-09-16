import { create } from 'zustand'
import { tagsApi, type Tag, type TagCategory } from '@/api/tags'
import { apiErrorMessage } from '@/api/client'

/**
 * The tag vocabulary, loaded whole.
 *
 * Unpaged deliberately — a personal vocabulary is tens to low hundreds of rows, and the
 * whole point of holding it here is that autocomplete can offer every tag the user has.
 * A paged catalogue would mean the picker could not suggest a tag they definitely own.
 *
 * Every await is guarded against a stale response, the pattern from `stores/library.ts`.
 */

interface TagState {
  tags: Tag[]
  categories: TagCategory[]
  loading: boolean
  error: string | null
  /** Whether a fetch has ever completed. Not the same as "has tags" — a new user has none. */
  loaded: boolean

  load: () => Promise<void>
  /**
   * Fetch once per session.
   *
   * Several unrelated places need the catalogue — the filter bar, the detail panel's
   * tag box, the bulk toolbar — and each mounting is not a reason to ask again.
   * Cleared by `reset()`, so the next person to sign in does fetch.
   */
  ensureLoaded: () => void
  /** Fold a tag the server just returned into the catalogue, without a refetch. */
  remember: (tags: Tag[]) => void
  create: (name: string, categoryId?: string | null) => Promise<void>
  rename: (id: string, name: string) => Promise<void>
  /** Move a tag into a category, or out of every category with null. */
  recategorise: (id: string, categoryId: string | null) => Promise<void>
  remove: (id: string) => Promise<void>
  createCategory: (name: string, parentId?: string | null) => Promise<void>
  updateCategory: (
    id: string,
    changes: { name?: string; parent_category_id?: string | null }
  ) => Promise<void>
  removeCategory: (id: string) => Promise<void>
  reset: () => void
}

let requestToken = 0

export const useTagStore = create<TagState>((set, get) => ({
  tags: [],
  categories: [],
  loading: false,
  error: null,
  loaded: false,

  ensureLoaded() {
    const { loaded, loading } = get()
    if (loaded || loading) return
    void get().load()
  },

  async load() {
    const token = ++requestToken
    set({ loading: true, error: null })
    try {
      const [tags, categories] = await Promise.all([
        tagsApi.list(),
        tagsApi.listCategories(),
      ])
      if (token !== requestToken) return
      set({ tags, categories, loading: false, loaded: true })
    } catch (error) {
      if (token !== requestToken) return
      set({ loading: false, error: apiErrorMessage(error, 'Could not load your tags') })
    }
  },

  remember(incoming) {
    // Tagging an asset can create a tag. Merging the server's rows here keeps
    // autocomplete current without a round trip, and keyed by id so a tag that already
    // existed is not duplicated in the list.
    set((state) => {
      const byId = new Map(state.tags.map((t) => [t.id, t]))
      incoming.forEach((tag) => {
        const existing = byId.get(tag.id)
        // Keep the count we already knew; the asset-level shape does not carry one.
        byId.set(tag.id, { ...existing, ...tag })
      })
      return { tags: [...byId.values()].sort((a, b) => a.name.localeCompare(b.name)) }
    })
  },

  async create(name, categoryId) {
    const token = requestToken
    try {
      const created = await tagsApi.create(name, categoryId)
      if (token !== requestToken) return
      // `POST /tags` is get-or-create, so this can return a tag that already exists.
      // `remember` merges by id rather than appending, which is exactly right for that.
      get().remember([created])
    } catch (error) {
      if (token !== requestToken) return
      set({ error: apiErrorMessage(error, 'Could not create that tag') })
      throw error
    }
  },

  async rename(id, name) {
    const token = requestToken
    const previous = get().tags
    set((state) => ({
      tags: state.tags.map((t) => (t.id === id ? { ...t, name } : t)),
    }))
    try {
      const saved = await tagsApi.rename(id, name)
      if (token !== requestToken) return
      set((state) => ({
        tags: state.tags.map((t) => (t.id === id ? { ...t, ...saved } : t)),
      }))
    } catch (error) {
      // A rename can be refused — the name may already be taken, case-insensitively —
      // so the optimistic edit has to come back out. Guarded: a rejection landing after
      // `reset()` would otherwise put the previous user's vocabulary back into a store
      // that was emptied on sign-out.
      if (token !== requestToken) return
      set({ tags: previous, error: apiErrorMessage(error, 'Could not rename that tag') })
      throw error
    }
  },

  async recategorise(id, categoryId) {
    const token = requestToken
    const previous = get().tags
    set((state) => ({
      tags: state.tags.map((t) => (t.id === id ? { ...t, category_id: categoryId } : t)),
    }))
    try {
      const saved = await tagsApi.recategorise(id, categoryId)
      if (token !== requestToken) return
      set((state) => ({
        tags: state.tags.map((t) => (t.id === id ? { ...t, ...saved } : t)),
      }))
    } catch (error) {
      if (token !== requestToken) return
      set({ tags: previous, error: apiErrorMessage(error, 'Could not move that tag') })
      throw error
    }
  },

  async remove(id) {
    const token = requestToken
    const previous = get().tags
    set((state) => ({ tags: state.tags.filter((t) => t.id !== id) }))
    try {
      await tagsApi.remove(id)
    } catch (error) {
      if (token !== requestToken) return
      set({ tags: previous, error: apiErrorMessage(error, 'Could not delete that tag') })
      throw error
    }
  },

  async createCategory(name, parentId) {
    const token = requestToken
    try {
      const created = await tagsApi.createCategory(name, parentId)
      if (token !== requestToken) return
      set((state) => ({ categories: [...state.categories, created] }))
    } catch (error) {
      if (token !== requestToken) return
      set({ error: apiErrorMessage(error, 'Could not create that category') })
      throw error
    }
  },

  async updateCategory(id, changes) {
    const token = requestToken
    const previous = get().categories
    set((state) => ({
      categories: state.categories.map((c) => (c.id === id ? { ...c, ...changes } : c)),
    }))
    try {
      const saved = await tagsApi.updateCategory(id, changes)
      if (token !== requestToken) return
      set((state) => ({
        categories: state.categories.map((c) => (c.id === id ? { ...c, ...saved } : c)),
      }))
    } catch (error) {
      // The server refuses a move that would put a category inside itself. The
      // optimistic edit has to come out, or the tree renders a cycle the API rejected.
      if (token !== requestToken) return
      set({
        categories: previous,
        error: apiErrorMessage(error, 'Could not update that category'),
      })
      throw error
    }
  },

  async removeCategory(id) {
    const token = requestToken
    const previous = get().categories
    set((state) => ({ categories: state.categories.filter((c) => c.id !== id) }))
    try {
      await tagsApi.removeCategory(id)
      // The server lifts orphaned children and tags to the top level rather than
      // cascading, so both lists are now stale in a way this store cannot derive.
      void get().load()
    } catch (error) {
      if (token !== requestToken) return
      set({
        categories: previous,
        error: apiErrorMessage(error, 'Could not delete that category'),
      })
      throw error
    }
  },

  reset() {
    requestToken += 1
    set({ tags: [], categories: [], loading: false, error: null, loaded: false })
  },
}))
