import { useEffect, useState } from 'react'

/**
 * Whether a CSS media query matches right now.
 *
 * Seeded synchronously inside `useState` rather than from an effect. The asset panel
 * picks its whole chrome from this — docked column or full-screen sheet — and a first
 * render that guessed wrong would flash a sheet over the library before settling.
 *
 * jsdom ships no `matchMedia`; it is stubbed in `test-setup.ts` alongside the other two
 * layout APIs it lacks, rather than guarded here.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)

  useEffect(() => {
    const list = window.matchMedia(query)
    // Re-read on subscribe: the viewport can change between the first render and this
    // effect — a rotation, or devtools opening — and the listener would never hear
    // about a transition it was not yet attached for.
    setMatches(list.matches)
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches)
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])

  return matches
}
