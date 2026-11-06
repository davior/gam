import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import AssetView from '@/views/AssetView'
import LibraryView from '@/views/LibraryView'
import PlaylistView from '@/views/PlaylistView'
import PlaylistsView from '@/views/PlaylistsView'
import SearchView from '@/views/SearchView'
import SettingsView from '@/views/SettingsView'
import { loadConfig } from '@/api/config'
import { setUnauthorizedHandler } from '@/api/client'
import { useAppThemeStore } from '@/stores/appTheme'
import { useAuthStore } from '@/stores/auth'
import ThemeBackdrop from '@/theme/ThemeBackdrop'
import { applyThemeToDom, cachedTheme } from '@/theme/apply'

export default function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)
  const status = useAuthStore((s) => s.status)
  const loadTheme = useAppThemeStore((s) => s.load)

  // The last theme this browser wore here, before anything else draws: no flash of the
  // plain look while the suite is asked (M7).
  useEffect(() => {
    applyThemeToDom(cachedTheme())
  }, [])

  // GAM's own theme choice, kept by Gecko Suite. Quietly nothing if the suite is away.
  useEffect(() => {
    if (status === 'authenticated') void loadTheme()
  }, [status, loadTheme])

  // One identity check on load. Everything below the shell can assume it has run.
  useEffect(() => {
    void bootstrap()
    // Started here rather than lazily at the sign-in button, so the address of the
    // login page is already in hand by the time anybody clicks it. `loadConfig` is
    // memoised, so this is one request however many callers ask.
    void loadConfig().catch(() => {
      // A failure is not fatal here — `RequireAuth` already reports an unreachable
      // backend, and a later attempt refetches.
    })
  }, [bootstrap])

  // Wired here rather than inside `api/client.ts`, which cannot import the auth store:
  // the store imports `api/auth.ts`, which imports the client. A callback set from the
  // one place that already depends on both keeps that cycle from existing.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      // Only for a session that *was* good. `bootstrap()` gets a 401 for every visitor
      // who is not signed in, and that is the anonymous path RequireAuth already
      // handles with a sign-in button — bouncing them to the Notes login instead would
      // turn "you are not signed in" into a redirect nobody asked for.
      const auth = useAuthStore.getState()
      if (auth.status !== 'authenticated') return false
      auth.signOut()
      return true
    })
  }, [])

  return (
    <>
      <ThemeBackdrop />
      <Routes>
        <Route path="/" element={<Navigate to="/library" replace />} />
        <Route
          path="/library"
          element={
            <RequireAuth>
              <AppShell>
                <LibraryView />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route
          path="/search"
          element={
            <RequireAuth>
              <AppShell>
                <SearchView />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route
          path="/settings"
          element={
            <RequireAuth>
              <AppShell>
                <SettingsView />
              </AppShell>
            </RequireAuth>
          }
        />
        {/* Above the catch-all, which otherwise swallows this into a silent redirect.
          GN-4 specifies this path as the Notes→GAM asset reference. */}
        <Route
          path="/a/:id"
          element={
            <RequireAuth>
              <AppShell>
                <AssetView />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route
          path="/playlists"
          element={
            <RequireAuth>
              <AppShell>
                <PlaylistsView />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route
          path="/playlists/:id"
          element={
            <RequireAuth>
              <AppShell>
                <PlaylistView />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/library" replace />} />
      </Routes>
    </>
  )
}
