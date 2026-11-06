import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowDown, ArrowLeft, ArrowUp, Pencil, Trash2 } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import { IMAGE_SECONDS, type PlaylistItem } from '@/api/playlists'
import PlaylistPlayer from '@/components/PlaylistPlayer'
import TimecodeInput from '@/components/TimecodeInput'
import { usePlaylistStore } from '@/stores/playlists'
import { formatDuration } from '@/utils/format'
import { useReadOnly } from '@/utils/useReadOnly'

/**
 * One playlist: the player, and the list it plays, editable.
 *
 * Editing an item's range pins the player to that item (`hold`), so the person can play
 * and scrub the moment and set its in and out points from the playhead, as in the Clip
 * tab, without the player moving on when it reaches the old out point.
 */
export default function PlaylistView() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const playlist = usePlaylistStore((s) => (s.current?.id === id ? s.current : null))
  const error = usePlaylistStore((s) => s.currentError)
  const open = usePlaylistStore((s) => s.open)
  const refresh = usePlaylistStore((s) => s.refresh)
  const rename = usePlaylistStore((s) => s.rename)
  const removeItem = usePlaylistStore((s) => s.removeItem)
  const move = usePlaylistStore((s) => s.move)
  const remove = usePlaylistStore((s) => s.remove)
  const readOnly = useReadOnly()

  const [index, setIndex] = useState(0)
  const [editing, setEditing] = useState<string | null>(null)
  const [player, setPlayer] = useState<HTMLVideoElement | HTMLAudioElement | null>(null)
  const [time, setTime] = useState(0)
  // One fresh read per item that fails to load: a second failure is a real one.
  const retried = useRef(new Set<string>())

  useEffect(() => {
    setIndex(0)
    setEditing(null)
    retried.current.clear()
    void open(id)
  }, [id, open])

  const items = playlist?.items ?? []
  // An item removed from under the player: stay in range.
  useEffect(() => {
    if (index >= items.length && items.length > 0) setIndex(items.length - 1)
  }, [index, items.length])

  const onMediaError = useCallback(
    (item: PlaylistItem) => {
      if (retried.current.has(item.id)) return
      retried.current.add(item.id)
      void refresh()
    },
    [refresh]
  )

  if (!playlist) {
    return (
      <div className="p-6 text-sm text-gray-500 dark:text-gray-400">
        {error ? (
          <p role="alert">
            {error}{' '}
            <Link to="/playlists" className="text-blue-700 underline">
              All playlists
            </Link>
          </p>
        ) : (
          'Loading…'
        )}
      </div>
    )
  }

  function startEditing(item: PlaylistItem, at: number) {
    setIndex(at)
    setEditing(item.id)
  }

  async function dropPlaylist() {
    if (!playlist) return
    if (
      !window.confirm(
        `Delete the playlist "${playlist.name}"? The assets in it are not deleted.`
      )
    )
      return
    await remove(playlist.id)
    navigate('/playlists')
  }

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 overflow-y-auto p-4">
      <div className="flex items-center gap-2">
        <Link to="/playlists" className="btn btn-ghost p-2" aria-label="All playlists">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <Title
          name={playlist.name}
          readOnly={readOnly}
          onSave={(name) => rename({ name })}
        />
        {!readOnly && (
          <button
            type="button"
            className="btn btn-ghost ml-auto p-2"
            onClick={() => void dropPlaylist()}
            aria-label="Delete playlist"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>
      {playlist.description && (
        <p className="text-sm text-gray-600 dark:text-gray-400">{playlist.description}</p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      <PlaylistPlayer
        items={items}
        index={Math.min(index, Math.max(0, items.length - 1))}
        onIndexChange={(next) => {
          setIndex(next)
          if (editing && items[next]?.id !== editing) setEditing(null)
        }}
        onPlayer={setPlayer}
        onTimeUpdate={setTime}
        onMediaError={onMediaError}
        hold={editing !== null}
      />

      <ol aria-label="Playlist items" className="space-y-1">
        {items.map((item, at) => (
          <li
            key={item.id}
            className={`rounded border p-2 ${
              at === index
                ? 'border-blue-400 bg-blue-50 dark:border-blue-500 dark:bg-blue-950/30'
                : 'border-gray-200 dark:border-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setIndex(at)
                  if (editing && editing !== item.id) setEditing(null)
                }}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                aria-label={`Play ${item.label || item.asset_name}`}
                aria-current={at === index ? 'true' : undefined}
              >
                <span className="w-6 shrink-0 text-right text-xs text-gray-400">
                  {at + 1}
                </span>
                <span className="h-9 w-14 shrink-0 overflow-hidden rounded bg-gray-100 dark:bg-gray-800">
                  {item.thumb_url && (
                    <img
                      src={item.thumb_url}
                      alt=""
                      className="h-full w-full object-cover"
                    />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                    {item.label || item.asset_name}
                  </span>
                  <span className="block truncate text-xs text-gray-500 dark:text-gray-400">
                    {describe(item)}
                  </span>
                </span>
              </button>
              <Link
                to={`/a/${item.asset_id}`}
                className="hidden text-xs text-blue-700 hover:underline sm:inline dark:text-blue-300"
              >
                Open asset
              </Link>
              {!readOnly && (
                <>
                  <button
                    type="button"
                    className="btn btn-ghost p-1.5"
                    onClick={() =>
                      editing === item.id ? setEditing(null) : startEditing(item, at)
                    }
                    aria-label={`Edit ${item.label || item.asset_name}`}
                    aria-pressed={editing === item.id}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost p-1.5"
                    disabled={at === 0}
                    onClick={() => {
                      void move(item.id, -1).catch(() => {})
                      setIndex(follow(index, at, -1))
                    }}
                    aria-label={`Move ${item.label || item.asset_name} up`}
                  >
                    <ArrowUp className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost p-1.5"
                    disabled={at === items.length - 1}
                    onClick={() => {
                      void move(item.id, 1).catch(() => {})
                      setIndex(follow(index, at, 1))
                    }}
                    aria-label={`Move ${item.label || item.asset_name} down`}
                  >
                    <ArrowDown className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost p-1.5"
                    onClick={() => {
                      if (editing === item.id) setEditing(null)
                      void removeItem(item.id).catch(() => {})
                    }}
                    aria-label={`Remove ${item.label || item.asset_name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </>
              )}
            </div>
            {item.note && (
              <p className="mt-1 pl-24 text-xs italic text-gray-500 dark:text-gray-400">
                {item.note}
              </p>
            )}
            {editing === item.id && (
              <ItemEditor
                item={item}
                currentTime={at === index ? time : item.start}
                onSeek={(seconds) => {
                  if (player) player.currentTime = seconds
                }}
                onDone={() => setEditing(null)}
              />
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

/** Where the playing item is after the item at `at` moves by `delta`: it moves with it,
 *  or it is the neighbour that swapped places. */
function follow(index: number, at: number, delta: -1 | 1): number {
  if (index === at) return at + delta
  if (index === at + delta) return at
  return index
}

function describe(item: PlaylistItem): string {
  if (item.asset_type === 'image') {
    return `Image · ${item.image_seconds ?? IMAGE_SECONDS}s`
  }
  const kind = item.asset_type === 'audio' ? 'Audio' : 'Video'
  if (item.in_point === null) {
    return `${kind} · whole${item.end !== null ? ` · ${formatDuration(item.end)}` : ''}`
  }
  const length = item.end !== null ? item.end - item.start : 0
  return `${kind} · ${formatDuration(item.start)}–${formatDuration(item.end)} (${formatDuration(length)})${
    item.label ? ` · ${item.asset_name}` : ''
  }`
}

function Title({
  name,
  readOnly,
  onSave,
}: {
  name: string
  readOnly: boolean
  onSave: (name: string) => Promise<void>
}) {
  const [draft, setDraft] = useState<string | null>(null)
  if (readOnly || draft === null) {
    return (
      <h2 className="flex min-w-0 items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
        <span className="truncate">{name}</span>
        {!readOnly && (
          <button
            type="button"
            className="btn btn-ghost p-1"
            onClick={() => setDraft(name)}
            aria-label="Rename playlist"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
        )}
      </h2>
    )
  }
  const save = async () => {
    const next = draft.trim()
    if (next && next !== name) await onSave(next).catch(() => {})
    setDraft(null)
  }
  return (
    <input
      autoFocus
      className="input min-w-0 flex-1 text-lg font-semibold"
      aria-label="Playlist name"
      value={draft}
      maxLength={255}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => void save()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') void save()
        if (e.key === 'Escape') setDraft(null)
      }}
    />
  )
}

/** An item's label and its range (or, for an image, how long it shows). */
function ItemEditor({
  item,
  currentTime,
  onSeek,
  onDone,
}: {
  item: PlaylistItem
  currentTime: number
  onSeek: (seconds: number) => void
  onDone: () => void
}) {
  const updateItem = usePlaylistStore((s) => s.updateItem)
  const isImage = item.asset_type === 'image'
  const max = item.asset_duration_seconds ?? undefined
  const [label, setLabel] = useState(item.label ?? '')
  const [whole, setWhole] = useState(item.in_point === null)
  const [start, setStart] = useState<number>(item.in_point ?? 0)
  const [end, setEnd] = useState<number>(item.out_point ?? item.end ?? 0)
  const [seconds, setSeconds] = useState<string>(
    String(item.image_seconds ?? IMAGE_SECONDS)
  )
  const [problem, setProblem] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function save() {
    setProblem(null)
    const changes: Parameters<typeof updateItem>[1] = { label: label.trim() || null }
    if (isImage) {
      const value = Number(seconds)
      if (!Number.isFinite(value) || value <= 0) {
        setProblem('Give the seconds as a number.')
        return
      }
      changes.image_seconds = value
    } else if (whole) {
      changes.in_point = null
      changes.out_point = null
    } else {
      if (end <= start) {
        setProblem('The out point must be after the in point.')
        return
      }
      changes.in_point = start
      changes.out_point = end
    }
    setSaving(true)
    try {
      await updateItem(item.id, changes)
      onDone()
    } catch (err) {
      setProblem(apiErrorMessage(err, 'Could not save that'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      role="group"
      aria-label="Edit item"
      className="mt-2 space-y-3 rounded bg-gray-50 p-3 dark:bg-gray-800/60"
    >
      <label className="block text-xs text-gray-600 dark:text-gray-400">
        Label
        <input
          className="input mt-1 w-full"
          value={label}
          maxLength={255}
          placeholder={item.asset_name}
          onChange={(e) => setLabel(e.target.value)}
        />
      </label>
      {isImage ? (
        <label className="block text-xs text-gray-600 dark:text-gray-400">
          Seconds on screen
          <input
            type="number"
            min={0.5}
            max={3600}
            step={0.5}
            className="input mt-1 w-28"
            value={seconds}
            onChange={(e) => setSeconds(e.target.value)}
          />
        </label>
      ) : (
        <>
          <label className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              checked={whole}
              onChange={(e) => setWhole(e.target.checked)}
            />
            Play the whole {item.asset_type}
          </label>
          {!whole && (
            <div className="grid gap-3 sm:grid-cols-2">
              <TimecodeInput
                label="In point"
                value={start}
                onChange={setStart}
                max={max}
                currentTime={currentTime}
                onSeek={onSeek}
              />
              <TimecodeInput
                label="Out point"
                value={end}
                onChange={setEnd}
                max={max}
                currentTime={currentTime}
                onSeek={onSeek}
              />
            </div>
          )}
        </>
      )}
      {problem && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          className="btn btn-primary px-3 py-1"
          disabled={saving}
          onClick={() => void save()}
        >
          Save
        </button>
        <button type="button" className="btn btn-secondary px-3 py-1" onClick={onDone}>
          Cancel
        </button>
      </div>
    </div>
  )
}
