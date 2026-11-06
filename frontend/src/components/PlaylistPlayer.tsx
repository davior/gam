import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Music, Play } from 'lucide-react'
import { IMAGE_SECONDS, type PlaylistItem } from '@/api/playlists'
import { formatDuration } from '@/utils/format'

interface Props {
  items: PlaylistItem[]
  index: number
  onIndexChange: (index: number) => void
  /** The media element now playing, for a range editor's "set to playhead". */
  onPlayer?: (player: HTMLVideoElement | HTMLAudioElement | null) => void
  onTimeUpdate?: (seconds: number) => void
  /** A file would not load. Signed URLs expire, so the page reads the playlist again. */
  onMediaError?: (item: PlaylistItem) => void
  /** While a range is being edited the player stays on its item: reaching the out point
   *  must not jump away from the moment being trimmed. */
  hold?: boolean
}

/** How long a missing item is shown before moving on, so it is noticed and not hidden. */
const SKIP_MISSING_MS = 1500

function tryPlay(element: HTMLMediaElement) {
  try {
    // A promise in browsers, which rejects when autoplay is refused; nothing to do then
    // but leave the controls for the person to press.
    const result = element.play() as Promise<void> | undefined
    result?.catch?.(() => {})
  } catch {
    // Not implemented (jsdom).
  }
}

/**
 * Plays a playlist one item at a time.
 *
 * Ranges are enforced here, client-side, as a clip's are in the asset view: the server
 * serves whole files (`media/ranged.py`). A video or audio item seeks to its start once
 * the browser knows the duration (setting `currentTime` earlier is silently ignored) and
 * moves on when it reaches its end or the file ends. An image stays up for its seconds.
 *
 * Nothing plays until the person starts it, since browsers refuse sound that starts on
 * its own. From then on each item starts the next, until the last one ends.
 */
export default function PlaylistPlayer({
  items,
  index,
  onIndexChange,
  onPlayer,
  onTimeUpdate,
  onMediaError,
  hold = false,
}: Props) {
  const [running, setRunning] = useState(false)
  const item = items[index] as PlaylistItem | undefined
  const advancing = useRef(false)

  const next = useCallback(() => {
    if (advancing.current) return
    if (index + 1 < items.length) {
      advancing.current = true
      onIndexChange(index + 1)
    } else {
      setRunning(false)
    }
  }, [index, items.length, onIndexChange])

  useEffect(() => {
    advancing.current = false
  }, [index, item?.id])

  // An image, or an item with no file, moves on by the clock.
  useEffect(() => {
    if (!item || !running || hold) return
    let wait: number | null = null
    if (item.missing || !item.file_url) wait = SKIP_MISSING_MS
    else if (item.asset_type === 'image') wait = (item.end ?? IMAGE_SECONDS) * 1000
    if (wait === null) return
    const timer = window.setTimeout(next, wait)
    return () => window.clearTimeout(timer)
  }, [item, running, hold, next])

  const attach = useCallback(
    (element: HTMLVideoElement | HTMLAudioElement | null) => {
      onPlayer?.(element)
      if (!element || !item) return
      const start = () => {
        element.currentTime = item.start
        if (running) tryPlay(element)
      }
      if (element.readyState >= 1) start()
      else element.addEventListener('loadedmetadata', start, { once: true })
    },
    // `running` is read at attach time only: pressing play on the element itself is
    // what starts it, and re-attaching on every change would seek back to the start.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [item?.id, item?.start, item?.file_url, onPlayer]
  )

  if (!item) {
    return (
      <div className="flex aspect-video items-center justify-center rounded bg-black/80 text-sm text-gray-300">
        This playlist is empty. Add videos, audio or images from the library.
      </div>
    )
  }

  const handleTime = (e: React.SyntheticEvent<HTMLMediaElement>) => {
    const time = e.currentTarget.currentTime
    onTimeUpdate?.(time)
    if (!hold && item.end !== null && time >= item.end) {
      e.currentTarget.pause()
      next()
    }
  }

  // `key` is passed on each element, not spread: React wants it written out.
  const media = {
    ref: attach,
    src: item.file_url ?? undefined,
    controls: true,
    preload: 'metadata' as const,
    onTimeUpdate: handleTime,
    onPlay: () => setRunning(true),
    onEnded: () => {
      if (!hold) next()
    },
    onError: () => onMediaError?.(item),
  }

  let stage
  if (item.missing || !item.file_url) {
    stage = (
      <div className="flex h-full items-center justify-center text-sm text-gray-300">
        {item.asset_name}: this file is missing from storage.
      </div>
    )
  } else if (item.asset_type === 'video') {
    stage = (
      <video
        key={item.id}
        {...media}
        poster={item.thumb_url ?? undefined}
        className="h-full w-full object-contain"
      />
    )
  } else if (item.asset_type === 'audio') {
    stage = (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6">
        {item.thumb_url ? (
          <img
            src={item.thumb_url}
            alt=""
            className="max-h-[60%] rounded object-contain"
          />
        ) : (
          <Music className="h-16 w-16 text-gray-400" aria-hidden />
        )}
        <audio key={item.id} {...media} className="w-full max-w-lg" />
      </div>
    )
  } else {
    stage = (
      <img
        key={item.id}
        src={item.file_url}
        alt={item.label || item.asset_name}
        onError={() => onMediaError?.(item)}
        className="h-full w-full object-contain"
      />
    )
  }

  const length = item.end !== null ? item.end - item.start : null
  return (
    <div className="space-y-2">
      <div className="relative aspect-video max-h-[60vh] w-full overflow-hidden rounded bg-black">
        {stage}
        {!running && (item.asset_type === 'image' || item.missing || !item.file_url) && (
          <button
            type="button"
            onClick={() => setRunning(true)}
            className="absolute inset-0 m-auto flex h-14 w-14 items-center justify-center rounded-full bg-black/60 text-white hover:bg-black/80"
            aria-label="Play the playlist from here"
          >
            <Play className="h-6 w-6" />
          </button>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn btn-ghost p-1.5"
          disabled={index === 0}
          onClick={() => onIndexChange(index - 1)}
          aria-label="Previous item"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div className="min-w-0 flex-1 text-sm">
          <p className="truncate font-medium text-gray-900 dark:text-gray-100">
            {item.label || item.asset_name}
          </p>
          <p className="truncate text-xs text-gray-500 dark:text-gray-400">
            {index + 1} of {items.length}
            {item.label ? ` · ${item.asset_name}` : ''}
            {item.asset_type !== 'image' && item.in_point !== null
              ? ` · ${formatDuration(item.start)}–${formatDuration(item.end)}`
              : ''}
            {length !== null ? ` · ${formatDuration(length)}` : ''}
          </p>
        </div>
        <button
          type="button"
          className="btn btn-ghost p-1.5"
          disabled={index + 1 >= items.length}
          onClick={() => onIndexChange(index + 1)}
          aria-label="Next item"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}
