import { useCallback, useEffect, useState } from 'react'
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from 'react'

const STORAGE_KEY = 'gam.detailPanelWidth'

/** 30rem. Wide enough for a 16:9 video and a readable transcript side by side. */
const DEFAULT_WIDTH = 480
/** 22rem. Below this the tab strip wraps and the metadata rows start truncating. */
const MIN_WIDTH = 352
/** How far one arrow-key press moves the edge. */
const KEYBOARD_STEP = 16

/**
 * Never more than 60% of the window, and never more than 56rem — past that the panel
 * stops being a companion to the library and starts being a worse full-screen view.
 * Read from `window` on demand because it changes when the window is resized.
 */
function ceiling(): number {
  return Math.max(Math.min(window.innerWidth * 0.6, 896), MIN_WIDTH)
}

function clamp(width: number, max: number): number {
  return Math.min(Math.max(width, MIN_WIDTH), max)
}

function stored(): number | null {
  try {
    const raw = Number(window.localStorage.getItem(STORAGE_KEY))
    return Number.isFinite(raw) && raw > 0 ? raw : null
  } catch {
    // localStorage throws outright in some privacy modes. A remembered width is not
    // worth failing to render the panel over.
    return null
  }
}

export interface ResizablePanel {
  width: number
  min: number
  max: number
  dragging: boolean
  onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void
  onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => void
  reset: () => void
}

/**
 * The width of a panel docked against the right edge, dragged by a handle on its left
 * edge and remembered across sessions.
 */
export function useResizablePanel(): ResizablePanel {
  const [max, setMax] = useState(() => ceiling())
  const [width, setWidth] = useState(() => clamp(stored() ?? DEFAULT_WIDTH, ceiling()))
  const [dragging, setDragging] = useState(false)

  // A narrowed window can put the remembered width past the new ceiling, which would
  // leave the library with no room at all.
  useEffect(() => {
    const onResize = () => {
      const limit = ceiling()
      setMax(limit)
      setWidth((current) => clamp(current, limit))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Written when the drag ends rather than on every move: a drag produces a width per
  // frame, and none of the intermediate ones is worth persisting.
  useEffect(() => {
    if (dragging) return
    try {
      window.localStorage.setItem(STORAGE_KEY, String(width))
    } catch {
      // Same privacy-mode case as the read.
    }
  }, [width, dragging])

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    // Without this the browser starts a text selection in the library behind the handle.
    event.preventDefault()
    const handle = event.currentTarget
    const { pointerId } = event

    // Pointer capture keeps the move events coming to the handle even when the cursor
    // outruns it, which it does on any fast drag.
    handle.setPointerCapture(pointerId)
    setDragging(true)

    const onMove = (move: PointerEvent) => {
      // The panel is flush against the right edge, so its width is the gap between the
      // pointer and that edge.
      setWidth(clamp(window.innerWidth - move.clientX, ceiling()))
    }
    const onUp = () => {
      setDragging(false)
      handle.removeEventListener('pointermove', onMove)
      handle.removeEventListener('pointerup', onUp)
      handle.removeEventListener('pointercancel', onUp)
      if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId)
    }

    handle.addEventListener('pointermove', onMove)
    handle.addEventListener('pointerup', onUp)
    handle.addEventListener('pointercancel', onUp)
  }, [])

  const onKeyDown = useCallback((event: ReactKeyboardEvent<HTMLElement>) => {
    const limit = ceiling()
    // The handle sits on the panel's left edge, so left is "more panel".
    if (event.key === 'ArrowLeft') setWidth((w) => clamp(w + KEYBOARD_STEP, limit))
    else if (event.key === 'ArrowRight') setWidth((w) => clamp(w - KEYBOARD_STEP, limit))
    else if (event.key === 'Home') setWidth(MIN_WIDTH)
    else if (event.key === 'End') setWidth(limit)
    else return
    event.preventDefault()
  }, [])

  const reset = useCallback(() => setWidth(clamp(DEFAULT_WIDTH, ceiling())), [])

  return { width, min: MIN_WIDTH, max, dragging, onPointerDown, onKeyDown, reset }
}
