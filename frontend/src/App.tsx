import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import LibraryView from '@/views/LibraryView'
import SettingsView from '@/views/SettingsView'
import { useAuthStore } from '@/stores/auth'

export default function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap)

  // One identity check on load. Everything below the shell can assume it has run.
  useEffect(() => {
    void bootstrap()
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
