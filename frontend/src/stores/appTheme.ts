import { create } from 'zustand'
import { loadConfig } from '@/api/config'
import { themesApi, type Theme, type ThemeApp } from '@/api/themes'
import { applyThemeToDom, cacheTheme, cachedTheme } from '@/theme/apply'
import { useThemeStore } from '@/stores/theme'

/**
 * Which theme this app wears (M7), chosen per app and kept by the suite.
 *
 * With none chosen, the app is its plain light or dark (`stores/theme.ts`), exactly as
 * before themes. A chosen theme brings its own mode, so the header's light/dark toggle
 * is hidden while one is worn.
 *
 * Copied from Gecko Suite's `stores/appTheme.ts`; `APP` and asking for the suite's
 * address first are the only differences.
 */

export const APP: ThemeApp = 'gam'

type Status = 'idle' | 'loading' | 'ready' | 'unavailable'

interface AppThemeState {
  themes: Theme[]
  selected: Theme | null
  status: Status
  /** Draw the cached theme, then ask the suite for the real choice and the list. */
  load: () => Promise<void>
  choose: (themeId: string | null) => Promise<void>
  /** After an edit on the Themes page, so a worn theme redraws with its new values. */
  refresh: () => Promise<void>
  reset: () => void
}

function wear(theme: Theme | null) {
  if (theme) {
    applyThemeToDom(theme)
  } else {
    applyThemeToDom(null)
    // Back to the plain mode this browser had chosen.
    const plain = useThemeStore.getState()
    plain.setTheme(plain.theme)
  }
  cacheTheme(theme)
}

let generation = 0

export const useAppThemeStore = create<AppThemeState>((set, get) => ({
  themes: [],
  selected: null,
  status: 'idle',

  async load() {
    const mine = ++generation
    const cached = cachedTheme()
    if (cached) applyThemeToDom(cached)
    set({ status: 'loading' })
    try {
      // Where the suite is comes from GAM's own config; without one there are no themes.
      const config = await loadConfig()
      if (!config.suite_base_url) throw new Error('no suite')
      const [themes, selected] = await Promise.all([
        themesApi.list(),
        themesApi.selected(APP),
      ])
      if (mine !== generation) return
      set({ themes, selected, status: 'ready' })
      wear(selected)
    } catch {
      if (mine !== generation) return
      // The suite is down or unreachable: keep whatever is drawn, say nothing.
      set({ status: 'unavailable' })
    }
  },

  async choose(themeId) {
    const mine = ++generation
    const selected = await themesApi.choose(APP, themeId)
    if (mine !== generation) return
    set({ selected })
    wear(selected)
  },

  async refresh() {
    await get().load()
  },

  reset() {
    generation++
    applyThemeToDom(null)
    cacheTheme(null)
    set({ themes: [], selected: null, status: 'idle' })
  },
}))
