import { create } from 'zustand'
import { generateApi, type GenerationModel } from '@/api/generate'
import { apiErrorMessage } from '@/api/client'

/**
 * The generation catalogue — the active fal endpoints everyone may use — loaded once.
 *
 * Three forms read it (the add panel, the selection bar, an asset's Generate tab), and
 * two of those are mounted on the library page at the same time; each mounting is not a
 * reason to ask again. Active rows only: an inactive one is something an admin parked,
 * and the Settings panel that manages those fetches its own list with them included.
 *
 * Guarded against stale responses the way `stores/tags.ts` is.
 */

interface GenerationState {
  models: GenerationModel[]
  loading: boolean
  /** Whether a fetch has ever completed. Not "has models" — an empty catalogue is real. */
  loaded: boolean
  error: string | null

  load: () => Promise<void>
  /** Fetch once per session. Cleared by `reset()`, so the next person does fetch. */
  ensureLoaded: () => void
  reset: () => void
}

let requestToken = 0

export const useGenerationStore = create<GenerationState>((set, get) => ({
  models: [],
  loading: false,
  loaded: false,
  error: null,

  ensureLoaded() {
    const { loaded, loading } = get()
    if (loaded || loading) return
    void get().load()
  },

  async load() {
    const token = ++requestToken
    set({ loading: true, error: null })
    try {
      const models = await generateApi.models()
      if (token !== requestToken) return
      set({ models, loading: false, loaded: true })
    } catch (error) {
      if (token !== requestToken) return
      set({
        loading: false,
        error: apiErrorMessage(error, 'Could not load the generation models'),
      })
    }
  },

  reset() {
    requestToken += 1
    set({ models: [], loading: false, loaded: false, error: null })
  },
}))
