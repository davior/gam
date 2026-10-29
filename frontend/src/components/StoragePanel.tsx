import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { AlertTriangle, Check, HardDrive, RefreshCw, Trash2 } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { apiErrorMessage } from '@/api/client'
import {
  storageApi,
  type ServerUsage,
  type StorageLevel,
  type StorageSummary,
  type Volume,
} from '@/api/storage'
import { formatBytes } from '@/utils/format'

/**
 * What the disk holds and what is left of it.
 *
 * The library half answers "what could I delete", which is why the largest files are
 * links. The disk half is an administrator's: `server` is null for everyone else, who
 * still gets the level — it is what explains a refused upload.
 */

const TYPE_LABELS: Record<string, string> = {
  video: 'Video',
  image: 'Images',
  audio: 'Audio',
  document: 'Documents',
}

// The first two slots of the validated categorical palette (the dataviz skill's
// reference instance), in its order: adjacent in the bar, so they are the pair it
// checked for colour-blind separation. Each mode has its own step, not a flip.
const MEDIA_FILL = 'bg-[#2a78d6] dark:bg-[#3987e5]'
const DATABASE_FILL = 'bg-[#eb6834] dark:bg-[#d95926]'
// Neutral rather than a third hue: what is not GAM's is context, not a series.
const OTHER_FILL = 'bg-gray-400 dark:bg-gray-500'
const FREE_FILL = 'bg-gray-100 dark:bg-gray-700'
// Hatched over the free track. "Reserved" is neither used nor writable, and a texture
// says so without a second gray that would read as more of "other".
const RESERVED_FILL = `${FREE_FILL} text-gray-400 dark:text-gray-500`
const RESERVED_STYLE: CSSProperties = {
  backgroundImage:
    'repeating-linear-gradient(135deg, currentColor 0 1.5px, transparent 1.5px 5px)',
}

// Below this share of the disk a segment is left out of the bar (it stays in the
// legend). A database of a few megabytes on a terabyte disk is a fraction of a pixel,
// and drawing it would leave only its two gaps — a mark that reads as something.
const MIN_SHARE = 0.002

interface Segment {
  key: string
  label: string
  bytes: number
  className: string
  style?: CSSProperties
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString()} ${noun}${count === 1 ? '' : 's'}`
}

function volumeTitle(volume: Volume, only: boolean): string {
  if (only) return 'Server disk'
  if (volume.roles.includes('media')) return 'Media disk'
  if (volume.roles.includes('database')) return 'Database disk'
  return 'Working space'
}

const ROLE_NAMES: Record<string, string> = {
  media: 'media',
  database: 'the database',
  working: 'temporary files',
}

function holds(volume: Volume): string {
  const names = volume.roles.map((role) => ROLE_NAMES[role] ?? role)
  if (names.length === 1) return `Holds ${names[0]}`
  return `Holds ${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

function segmentsFor(volume: Volume, server: ServerUsage): Segment[] {
  const media = volume.roles.includes('media') ? server.media.bytes : 0
  const database = volume.roles.includes('database') ? (server.database_bytes ?? 0) : 0
  const ours = media + database
  const reserved = Math.max(volume.total_bytes - volume.used_bytes - volume.free_bytes, 0)

  return [
    { key: 'media', label: 'GAM media', bytes: media, className: MEDIA_FILL },
    { key: 'database', label: 'GAM database', bytes: database, className: DATABASE_FILL },
    {
      key: 'other',
      // On a volume holding nothing of GAM's own, everything used is "in use" — calling
      // it "other" would imply there were something to compare it with.
      label: ours > 0 ? 'Everything else' : 'In use',
      bytes: Math.max(volume.used_bytes - ours, 0),
      className: OTHER_FILL,
    },
    {
      key: 'reserved',
      label: 'Reserved by the system',
      bytes: reserved,
      className: RESERVED_FILL,
      style: RESERVED_STYLE,
    },
    { key: 'free', label: 'Free', bytes: volume.free_bytes, className: FREE_FILL },
  ].filter((segment) => segment.bytes > 0 || segment.key === 'free')
}

function DiskBar({ volume, segments }: { volume: Volume; segments: Segment[] }) {
  const total = volume.total_bytes || 1
  const drawn = segments.filter((s) => s.bytes / total >= MIN_SHARE)

  return (
    // flex-grow by bytes rather than a percentage width: the 2px gaps come out of the
    // space first and the rest is shared in proportion, so the bar never overflows.
    <div
      className="flex h-3 w-full gap-[2px] overflow-hidden rounded"
      role="img"
      aria-label={segments.map((s) => `${s.label} ${formatBytes(s.bytes)}`).join(', ')}
    >
      {drawn.map((segment) => (
        <div
          key={segment.key}
          className={segment.className}
          style={{ flexGrow: segment.bytes, flexBasis: 0, ...segment.style }}
          title={`${segment.label}: ${formatBytes(segment.bytes)}`}
        />
      ))}
    </div>
  )
}

function LevelNotice({
  level,
  server,
}: {
  level: StorageLevel
  server: ServerUsage | null
}) {
  if (level === 'ok') return null

  const critical = level === 'critical'
  // An admin is given the threshold; everybody else the consequence, without the
  // numbers they are not shown anywhere else either.
  const message = critical
    ? `The disk is nearly full. Uploads, imports and generations are paused until there is more than ${server ? formatBytes(server.reserve_bytes) : 'a safe margin'} free.`
    : server
      ? `Running low — under ${formatBytes(server.warn_free_bytes)} free.`
      : 'The server is running low on disk space.'

  return (
    <p
      className={
        critical
          ? 'flex items-start gap-1.5 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300'
          : 'flex items-start gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300'
      }
    >
      <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
      {message}
    </p>
  )
}

function VolumeBlock({
  volume,
  server,
  only,
}: {
  volume: Volume
  server: ServerUsage
  only: boolean
}) {
  const segments = segmentsFor(volume, server)
  const share = volume.total_bytes
    ? Math.round((volume.free_bytes / volume.total_bytes) * 100)
    : 0

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold text-gray-900 dark:text-gray-100">
            {volumeTitle(volume, only)}
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">{holds(volume)}</p>
        </div>
        <p className="text-right text-xs text-gray-900 dark:text-gray-100">
          {formatBytes(volume.free_bytes)} free of {formatBytes(volume.total_bytes)}
          <span className="text-gray-500 dark:text-gray-400"> ({share}%)</span>
        </p>
      </div>

      <DiskBar volume={volume} segments={segments} />

      <ul
        className="flex flex-wrap gap-x-4 gap-y-1 text-xs"
        aria-label={`What is on the ${volumeTitle(volume, only).toLowerCase()}`}
      >
        {segments.map((segment) => (
          <li key={segment.key} className="flex items-center gap-1.5">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-sm ${segment.className}`}
              style={segment.style}
              aria-hidden="true"
            />
            <span className="text-gray-500 dark:text-gray-400">{segment.label}</span>
            <span className="text-gray-900 dark:text-gray-100">
              {formatBytes(segment.bytes)}
            </span>
          </li>
        ))}
      </ul>

      {!volume.guarded && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Uploads and imports pass through here before they are stored. Nothing is kept
          here, so this disk does not count toward the warning.
          {volume.level !== 'ok' &&
            ' It is small enough that a large import may not fit.'}
        </p>
      )}
    </div>
  )
}

function StrayFiles({
  server,
  onCleaned,
}: {
  server: ServerUsage
  onCleaned: () => void
}) {
  const [working, setWorking] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { files, bytes } = server.stray

  const clean = async () => {
    if (
      !confirm(
        `Delete ${plural(files, 'stray file')} (${formatBytes(bytes)})? Nothing in the library uses ${files === 1 ? 'it' : 'them'}.`
      )
    ) {
      return
    }
    setWorking(true)
    setError(null)
    try {
      const done = await storageApi.cleanup()
      setResult(
        `Removed ${plural(done.removed.files, 'file')} (${formatBytes(done.removed.bytes)})` +
          (done.skipped ? `; ${done.skipped} could not be removed.` : '.')
      )
      onCleaned()
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not remove them'))
    } finally {
      setWorking(false)
    }
  }

  return (
    <div className="space-y-2 border-t border-gray-100 pt-3 dark:border-gray-800">
      {files > 0 ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-gray-600 dark:text-gray-400">
            {plural(files, 'file')} ({formatBytes(bytes)}) on disk that no asset refers to
            — left by interrupted uploads or failed jobs.
          </p>
          <button
            type="button"
            className="btn btn-secondary shrink-0 text-xs"
            disabled={working}
            onClick={() => void clean()}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Remove them
          </button>
        </div>
      ) : (
        <p className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
          <Check className="h-3.5 w-3.5" />
          No stray files.
        </p>
      )}
      {result && <p className="text-xs text-green-700 dark:text-green-400">{result}</p>}
      {error && <p className="text-xs text-red-700 dark:text-red-400">{error}</p>}
    </div>
  )
}

export default function StoragePanel() {
  const [summary, setSummary] = useState<StorageSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A refresh can overtake the load before it; only the latest answer is shown.
  const latest = useRef(0)
  const section = useRef<HTMLElement>(null)
  const { hash } = useLocation()

  const load = useCallback(() => {
    const request = ++latest.current
    setLoading(true)
    storageApi
      .summary()
      .then((next) => {
        if (request !== latest.current) return
        setSummary(next)
        setError(null)
      })
      .catch((err) => {
        if (request !== latest.current) return
        setError(apiErrorMessage(err, 'Could not load storage'))
      })
      .finally(() => {
        if (request === latest.current) setLoading(false)
      })
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // The header's warning links here. Scrolled once there is something to scroll to,
  // since the panels above it load at their own pace and move it as they do.
  const loaded = summary !== null
  useEffect(() => {
    if (loaded && hash === '#storage')
      section.current?.scrollIntoView?.({ block: 'start' })
  }, [loaded, hash])

  const library = summary?.library
  const server = summary?.server ?? null

  return (
    <section id="storage" ref={section} className="card scroll-mt-4 space-y-4 p-5">
      <header className="flex items-center gap-2">
        <HardDrive className="h-4 w-4 text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Storage
        </h2>
        <button
          type="button"
          className="btn btn-ghost ml-auto p-1.5"
          onClick={load}
          disabled={loading}
          aria-label="Refresh storage"
          title="Refresh"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </header>

      {error && (
        <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </p>
      )}

      {summary && <LevelNotice level={summary.level} server={server} />}

      {server &&
        server.volumes.map((volume) => (
          <VolumeBlock
            key={volume.roles.join('+')}
            volume={volume}
            server={server}
            only={server.volumes.length === 1}
          />
        ))}

      {library && (
        <div
          className={
            server
              ? 'space-y-3 border-t border-gray-100 pt-3 dark:border-gray-800'
              : 'space-y-3'
          }
        >
          <div>
            <h3 className="text-xs font-semibold text-gray-900 dark:text-gray-100">
              Your library
            </h3>
            <p className="mt-1 text-2xl font-semibold text-gray-900 dark:text-gray-100">
              {formatBytes(library.total_bytes)}
            </p>
          </div>

          {library.files === 0 ? (
            <p className="text-xs text-gray-600 dark:text-gray-400">
              Nothing stored yet.
            </p>
          ) : (
            <dl className="space-y-1 text-xs">
              {library.by_type.map((row) => (
                <div key={row.asset_type} className="flex justify-between">
                  <dt className="text-gray-500 dark:text-gray-400">
                    {TYPE_LABELS[row.asset_type] ?? row.asset_type}
                  </dt>
                  <dd className="text-gray-900 dark:text-gray-100">
                    {plural(row.files, 'file')} · {formatBytes(row.bytes)}
                  </dd>
                </div>
              ))}
              {library.versions.files > 0 && (
                <div className="flex justify-between">
                  <dt className="text-gray-500 dark:text-gray-400">Video versions</dt>
                  <dd className="text-gray-900 dark:text-gray-100">
                    {plural(library.versions.files, 'file')} ·{' '}
                    {formatBytes(library.versions.bytes)}
                  </dd>
                </div>
              )}
              {library.thumbnails.files > 0 && (
                <div className="flex justify-between">
                  <dt className="text-gray-500 dark:text-gray-400">Previews</dt>
                  <dd className="text-gray-900 dark:text-gray-100">
                    {formatBytes(library.thumbnails.bytes)}
                  </dd>
                </div>
              )}
            </dl>
          )}

          {library.largest.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold text-gray-900 dark:text-gray-100">
                Largest files
              </h3>
              <ol className="space-y-1 text-xs">
                {library.largest.map((asset) => (
                  <li
                    key={asset.id}
                    className="flex items-baseline justify-between gap-3"
                  >
                    <Link
                      to={`/a/${asset.id}`}
                      className="min-w-0 truncate text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
                    >
                      {asset.name}
                    </Link>
                    <span className="shrink-0 text-gray-500 dark:text-gray-400">
                      {TYPE_LABELS[asset.asset_type] ?? asset.asset_type} ·{' '}
                      <span className="text-gray-900 dark:text-gray-100">
                        {formatBytes(asset.size_bytes + asset.versions_bytes)}
                      </span>
                      {asset.versions_bytes > 0 && (
                        <> (incl. {formatBytes(asset.versions_bytes)} in versions)</>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {server && <StrayFiles server={server} onCleaned={load} />}
    </section>
  )
}
