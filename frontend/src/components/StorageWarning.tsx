import { useEffect, useState } from 'react'
import { HardDrive } from 'lucide-react'
import { Link } from 'react-router-dom'
import { storageApi, type StorageStatus } from '@/api/storage'
import { formatBytes } from '@/utils/format'

/**
 * A drive icon in the header, only while the disk is running out.
 *
 * Nothing at all while there is room: an always-on gauge is one more thing in the
 * header to learn to ignore, and a warning people have learned to ignore is no warning.
 * The endpoint is a `statvfs` and nothing more, so polling it costs nothing.
 */

const POLL_MS = 5 * 60 * 1000

function describe(status: StorageStatus): string {
  const free =
    status.free_bytes !== null ? ` — ${formatBytes(status.free_bytes)} free` : ''
  return status.level === 'critical'
    ? `Disk nearly full${free}. New uploads are paused.`
    : `Disk space low${free}`
}

export default function StorageWarning() {
  const [status, setStatus] = useState<StorageStatus | null>(null)

  useEffect(() => {
    let live = true
    const check = () => {
      storageApi
        .status()
        .then((next) => {
          if (live) setStatus(next)
        })
        // A failed check shows nothing rather than a warning about a disk it could
        // not read; the next poll tries again.
        .catch(() => {
          if (live) setStatus(null)
        })
    }
    check()
    const timer = setInterval(check, POLL_MS)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [])

  if (!status || status.level === 'ok') return null

  const label = describe(status)
  return (
    <Link
      to="/settings#storage"
      className={`btn btn-ghost p-2 ${
        status.level === 'critical'
          ? 'text-red-600 dark:text-red-400'
          : 'text-amber-600 dark:text-amber-400'
      }`}
      aria-label={label}
      title={label}
    >
      <HardDrive className="h-4 w-4" />
    </Link>
  )
}
