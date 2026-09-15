import { useState } from 'react'

/**
 * A saved-tick that clears itself.
 *
 * Lives here rather than in SettingsView because every settings panel wants it, and the
 * provider panel is in its own file — importing it back out of the view that renders it
 * would be a cycle.
 */
export function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [
    saved,
    () => {
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    },
  ]
}
