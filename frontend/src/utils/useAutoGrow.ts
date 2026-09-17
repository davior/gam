import { useLayoutEffect, useRef } from 'react'

/**
 * A textarea that grows to fit its content instead of scrolling inside itself.
 *
 * The description and summary boxes used to be a fixed 80px with their own scrollbar, so
 * a forty-line description showed four lines. The panel scrolls; the fields inside it
 * should not have to.
 *
 * Pair with a `min-h-*` class and `resize-none` — the box sizes itself, so a drag handle
 * on it would only fight this.
 */
export function useAutoGrow(value: string) {
  const ref = useRef<HTMLTextAreaElement | null>(null)

  // A layout effect, not an effect: measuring after paint shows one frame at the wrong
  // height every time a different asset is loaded into the panel.
  useLayoutEffect(() => {
    const field = ref.current
    if (!field) return

    const fit = () => {
      // Collapse before measuring. `scrollHeight` never reports less than the element's
      // current height, so without this the box grows on every keystroke and never
      // shrinks back when text is deleted.
      field.style.height = 'auto'
      field.style.height = `${field.scrollHeight}px`
    }
    fit()

    // The panel is drag-resizable, so the same text rewraps to a different number of
    // lines without the value ever changing. Only width is acted on: `fit` sets the
    // height, and reacting to a height change would be a feedback loop.
    let width = field.clientWidth
    const observer = new ResizeObserver(() => {
      if (field.clientWidth === width) return
      width = field.clientWidth
      fit()
    })
    observer.observe(field)
    return () => observer.disconnect()
  }, [value])

  return ref
}
