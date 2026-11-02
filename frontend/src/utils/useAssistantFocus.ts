import { useEffect } from 'react'
import { useAssistantStore } from '@/stores/assistant'

/**
 * Tell the assistant which asset is open, so "this one" in a message means it.
 *
 * Cleared when the asset closes or the view goes away: a focus left behind would make
 * the next question about an asset nobody is looking at any more.
 */
export function useAssistantFocus(
  asset: { id: string; name: string } | null | undefined
) {
  const id = asset?.id ?? null
  const name = asset?.name ?? ''
  const setFocus = useAssistantStore((s) => s.setFocus)
  useEffect(() => {
    setFocus(id ? { id, name } : null)
    return () => setFocus(null)
  }, [id, name, setFocus])
}
