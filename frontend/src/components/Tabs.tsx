import { useId, useRef, useState } from 'react'
import type { ComponentType, KeyboardEvent, ReactNode } from 'react'

export interface TabSpec {
  id: string
  label: string
  icon?: ComponentType<{ className?: string }>
  /** Rendered beside the label — a segment count, a page count. */
  badge?: ReactNode
  content: ReactNode
}

interface Props {
  tabs: TabSpec[]
  className?: string
}

/**
 * A tab strip and its panels.
 *
 * The first UI primitive in this codebase — everything else is either a CSS class in
 * `main.css` or a one-off. It lives flat in `components/` rather than under a new `ui/`
 * directory because the house rule is no barrel files, which would make `ui/` nothing
 * but a longer import path.
 *
 * Every panel stays mounted and inactive ones are hidden, rather than rendering only the
 * active one. `TranscriptPanel` fetches on mount, so unmounting would refetch the
 * transcript — and lose its scroll position — every time you looked at the description
 * and came back.
 *
 * Which tab is active is held here, not lifted: the asset panel swaps assets underneath
 * it, and the useful behaviour is that a transcript stays open as you move between
 * videos. When the new asset has no such tab, `current` falls back to the first one.
 */
export default function Tabs({ tabs, className = '' }: Props) {
  const base = useId()
  const [active, setActive] = useState<string | undefined>(tabs[0]?.id)
  const stripRef = useRef<HTMLDivElement>(null)

  const current = tabs.some((tab) => tab.id === active) ? active : tabs[0]?.id
  if (tabs.length === 0) return null

  const tabId = (id: string) => `${base}-tab-${id}`
  const panelId = (id: string) => `${base}-panel-${id}`

  // Arrow keys move between tabs and take the focus with them, which is what makes a
  // roving tabindex worth having — Tab alone would only ever reach the active one.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex((tab) => tab.id === current)
    if (index === -1) return

    let next = index
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = tabs.length - 1
    else return

    event.preventDefault()
    setActive(tabs[next].id)
    // By position, not by id selector: React's `useId` produces ids containing colons,
    // which are not valid in a CSS selector without escaping.
    stripRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
  }

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${className}`}>
      <div
        ref={stripRef}
        role="tablist"
        onKeyDown={onKeyDown}
        className="flex shrink-0 gap-1 overflow-x-auto border-b border-gray-200 px-2 dark:border-gray-700"
      >
        {tabs.map((tab) => {
          const selected = tab.id === current
          const Icon = tab.icon
          return (
            <button
              key={tab.id}
              id={tabId(tab.id)}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={panelId(tab.id)}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActive(tab.id)}
              className={`-mb-px flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-xs font-medium transition-colors ${
                selected
                  ? 'border-blue-600 text-blue-700 dark:border-blue-400 dark:text-blue-300'
                  : 'border-transparent text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'
              }`}
            >
              {Icon && <Icon className="h-3.5 w-3.5" />}
              {tab.label}
              {tab.badge !== undefined && (
                <span className="text-[11px] text-gray-400 dark:text-gray-500">
                  {tab.badge}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {tabs.map((tab) => (
        <div
          key={tab.id}
          id={panelId(tab.id)}
          role="tabpanel"
          aria-labelledby={tabId(tab.id)}
          hidden={tab.id !== current}
          className={tab.id === current ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}
        >
          {tab.content}
        </div>
      ))}
    </div>
  )
}
