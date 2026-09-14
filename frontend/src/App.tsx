import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import LibraryView from '@/views/LibraryView'
import SearchView from '@/views/SearchView'
import SettingsView from '@/views/SettingsView'
import { loadConfig } from '@/api/config'
import { useAuthStore } from '@/stores/auth'

export default function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)

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

  return (
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
      <Route path="*" element={<Navigate to="/library" replace />} />
    </Routes>
  )
}
