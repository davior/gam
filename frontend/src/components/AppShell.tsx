import { useEffect, useState, type ReactNode } from 'react'
import { ListVideo, LogOut, Moon, Search, Settings, Sparkles, Sun } from 'lucide-react'
import { Link } from 'react-router-dom'
import { currentConfig, loadConfig } from '@/api/config'
import ActivityIndicator from '@/components/ActivityIndicator'
import AssistantPanel from '@/components/AssistantPanel'
import StorageWarning from '@/components/StorageWarning'
import { useAssistantStore } from '@/stores/assistant'
import favicon from '@/assets/favicon.svg'
import { useAppThemeStore } from '@/stores/appTheme'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import ThemePicker from '@/theme/ThemePicker'

interface Props {
  children: ReactNode
}

/**
 * The frame every view sits in.
 *
 * gecko-notes has no shared shell — each view rebuilds the same header/body skeleton,
 * and they have drifted. One component here means they cannot.
 */
export default function AppShell({ children }: Props) {
  const user = useAuthStore((s) => s.user)
  const signOut = useAuthStore((s) => s.signOut)
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggle)
  // A theme from the suite brings its own light or dark, so the toggle would fight it.
  const wearing = useAppThemeStore((s) => s.selected !== null)
  const suiteUrl = currentConfig()?.suite_base_url?.replace(/\/+$/, '') || null
  const assistantOpen = useAssistantStore((s) => s.open)
  const setAssistantOpen = useAssistantStore((s) => s.setOpen)
  const [readOnly, setReadOnly] = useState(() => currentConfig()?.read_only ?? false)

  useEffect(() => {
    let stale = false
    loadConfig()
      .then((config) => {
        if (!stale) setReadOnly(config.read_only)
      })
      // App already reports an unreachable backend; a missing banner adds nothing.
      .catch(() => {})
    return () => {
      stale = true
    }
  }, [])

  return (
    <div className="flex h-screen flex-col bg-gray-50 dark:bg-gray-900">
      {/* relative z-40: a glass theme blurs the header (assets/glass.css), and a blurred
          element is its own stacking layer, so the activity and theme popups' z-50 only
          orders them inside the header. Without a z-index of its own the whole header
          paints in document order, under the glass chips and cards of the page below it.
          40 keeps it under the full-screen detail view (50) and level with the assistant
          panel, which covers the page on a phone. */}
      <header className="relative z-40 shrink-0 border-b border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-800">
        <div className="flex items-center gap-3">
          <Link to="/library" className="flex items-center gap-3">
            <img src={favicon} alt="" aria-hidden="true" className="h-7 w-7" />
            <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              Gecko Asset Manager
            </h1>
          </Link>

          <div className="ml-auto flex items-center gap-3">
            {user && <ThemePicker editUrl={suiteUrl ? `${suiteUrl}/themes` : null} />}
            {!wearing && (
              <button
                type="button"
                onClick={toggleTheme}
                className="btn btn-ghost p-2"
                aria-label={
                  theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'
                }
              >
                {theme === 'dark' ? (
                  <Sun className="h-4 w-4" />
                ) : (
                  <Moon className="h-4 w-4" />
                )}
              </button>
            )}
            <Link to="/search" className="btn btn-ghost p-2" aria-label="Search">
              <Search className="h-4 w-4" />
            </Link>
            <Link
              to="/playlists"
              className="btn btn-ghost p-2"
              aria-label="Playlists"
              title="Playlists"
            >
              <ListVideo className="h-4 w-4" />
            </Link>
            {/* Not on an archive: it proposes changes, and an archive refuses them. */}
            {!readOnly && (
              <button
                type="button"
                onClick={() => setAssistantOpen(!assistantOpen)}
                className={`btn btn-ghost p-2 ${assistantOpen ? 'bg-gray-100 dark:bg-gray-700' : ''}`}
                aria-label="Assistant"
                aria-pressed={assistantOpen}
                title="Assistant"
              >
                <Sparkles className="h-4 w-4" />
              </button>
            )}
            <StorageWarning />
            <ActivityIndicator />
            <Link to="/settings" className="btn btn-ghost p-2" aria-label="Settings">
              <Settings className="h-4 w-4" />
            </Link>
            {/* No sign-out on an archive: it signs nobody in, so the next request
                would put the same user straight back. */}
            {user && !readOnly && (
              <>
                <span className="text-sm text-gray-600 dark:text-gray-400">
                  {user.username}
                </span>
                {/* `signOut` has existed since M2 and was called by nothing, so the only
                    way out of a session was to clear localStorage by hand. It already
                    does the whole fan-out — token, four stores, redirect to Notes. */}
                <button
                  type="button"
                  onClick={signOut}
                  className="btn btn-ghost p-2"
                  aria-label="Sign out"
                  title="Sign out"
                >
                  <LogOut className="h-4 w-4" />
                </button>
              </>
            )}
          </div>
        </div>
      </header>

      {readOnly && (
        // Said up front rather than left to the first refused save: an archive looks
        // exactly like the live app, and someone who edits it should know before they
        // start that nothing they do will be kept.
        <div
          role="status"
          className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-center text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          Read-only archive copy. Browse and play anything; changes are made in the live
          app.
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1 overflow-auto">{children}</main>
        {assistantOpen && !readOnly && <AssistantPanel />}
      </div>
    </div>
  )
}
