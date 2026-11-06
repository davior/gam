import { useEffect, useState } from 'react'
import { currentConfig, loadConfig } from '@/api/config'

/** Whether this is a read-only archive copy, where anything that writes is hidden. */
export function useReadOnly(): boolean {
  const [readOnly, setReadOnly] = useState(() => currentConfig()?.read_only ?? false)
  useEffect(() => {
    let stale = false
    loadConfig()
      .then((config) => {
        if (!stale) setReadOnly(config.read_only)
      })
      // The app already reports an unreachable backend.
      .catch(() => {})
    return () => {
      stale = true
    }
  }, [])
  return readOnly
}
