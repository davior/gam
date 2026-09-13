import type { ReactNode } from 'react'
import { Moon, Sun } from 'lucide-react'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

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
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggle)

  return (
    <div className="flex h-screen flex-col bg-gray-50 dark:bg-gray-900">
      <header className="shrink-0 border-b border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-800">
        <div className="flex items-center gap-3">
          <span className="text-xl" aria-hidden="true">
            🦎
          </span>
          <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            Asset Manager
          </h1>

          <div className="ml-auto flex items-center gap-3">
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
            {user && (
              <span className="text-sm text-gray-600 dark:text-gray-400">
                {user.username}
              </span>
            )}
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  )
}
