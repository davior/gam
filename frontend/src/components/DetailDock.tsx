import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { useMediaQuery } from '@/utils/useMediaQuery'
import { useResizablePanel } from '@/utils/useResizablePanel'

/**
 * Below this there is no room for a library and a panel at once, so the panel takes the
 * screen instead. Roughly a small laptop: two columns of tiles plus the narrowest useful
 * panel.
 */
const DOCKABLE = '(min-width: 1024px)'

interface Props {
  /** The panel's accessible name — the asset's own name. */
  label: string
  onClose: () => void
  children: ReactNode
}

/**
 * Where the asset panel sits, and nothing about what is in it.
 *
 * `AssetDetail` used to bake `fixed inset-0 … bg-black/50` into itself, which is why all
 * three of its callers got modal chrome whether it suited them or not — including
 * `/a/:id`, which rendered a floating dialog over an empty shell. Positioning lives here
 * now; the panel content fills whatever box it is handed.
 *
 * Wide enough, and it docks against the right edge with a drag handle, leaving the
 * library visible and scrollable beside it. Narrower, and it is a full-screen sheet.
 * That choice is made in JavaScript rather than with `lg:` classes because the two
 * genuinely differ — a backdrop and a scroll lock against a border and a pointer-driven
 * width — and branching keeps the drag listeners off entirely when they cannot apply.
 */
export default function DetailDock({ label, onClose, children }: Props) {
  const docked = useMediaQuery(DOCKABLE)
  const panel = useResizablePanel()

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // A sheet covering the screen must not let the library scroll underneath it. Docked,
  // the library is the point, so it keeps scrolling.
  useEffect(() => {
    if (docked) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [docked])

  if (!docked) {
    return (
      <div
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-white dark:bg-gray-800"
      >
        {children}
      </div>
    )
  }

  return (
    <>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the asset panel"
        aria-valuenow={Math.round(panel.width)}
        aria-valuemin={panel.min}
        aria-valuemax={Math.round(panel.max)}
        tabIndex={0}
        onPointerDown={panel.onPointerDown}
        onKeyDown={panel.onKeyDown}
        onDoubleClick={panel.reset}
        title="Drag to resize — double-click to reset"
        // touch-none so a drag on a touchscreen resizes instead of panning the page.
        className={`w-1.5 shrink-0 cursor-col-resize touch-none border-l border-gray-200 transition-colors focus:outline-none focus-visible:bg-blue-500/70 dark:border-gray-700 ${
          panel.dragging
            ? 'bg-blue-500/70'
            : 'bg-gray-100 hover:bg-blue-500/50 dark:bg-gray-800'
        }`}
      />
      <div
        role="dialog"
        aria-label={label}
        // No `aria-modal` here: docked, it sits beside the library rather than over it,
        // and the rest of the page is still usable.
        style={{ width: panel.width }}
        className="flex h-full shrink-0 flex-col overflow-hidden bg-white dark:bg-gray-800"
      >
        {children}
      </div>
    </>
  )
}
