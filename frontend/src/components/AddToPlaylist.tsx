import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ListPlus } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import type { PlaylistItemIn } from '@/api/playlists'
import { usePlaylistStore } from '@/stores/playlists'

const NEW = '__new__'

interface Props {
  /** What to add, in order. Empty disables the button (`emptyHint` says why). */
  items: PlaylistItemIn[]
  emptyHint?: string
  className?: string
}

/**
 * "Add to playlist": pick one of the person's playlists, or name a new one.
 *
 * Used from the library's selection (in the order things were picked) and from an
 * asset's page. A clip is sent as itself and the server stores its original and its
 * window, so nothing here needs to know what a clip is.
 */
export default function AddToPlaylist({ items, emptyHint, className = '' }: Props) {
  const playlists = usePlaylistStore((s) => s.playlists)
  const ensureLoaded = usePlaylistStore((s) => s.ensureLoaded)
  const create = usePlaylistStore((s) => s.create)
  const addTo = usePlaylistStore((s) => s.addTo)
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<string>('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<{ id: string; name: string } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    if (open) ensureLoaded()
  }, [open, ensureLoaded])

  // The first playlist, or "new" when there are none, once the list is in.
  useEffect(() => {
    if (open && !target) setTarget(playlists[0]?.id ?? NEW)
  }, [open, target, playlists])

  async function add() {
    setBusy(true)
    setProblem(null)
    try {
      const result =
        target === NEW ? await create(name.trim(), items) : await addTo(target, items)
      setDone({ id: result.id, name: result.name })
      setName('')
      setOpen(false)
    } catch (err) {
      setProblem(apiErrorMessage(err, 'Could not add to the playlist'))
    } finally {
      setBusy(false)
    }
  }

  const ready =
    items.length > 0 && (target !== NEW || name.trim() !== '') && target !== ''

  return (
    <div className={`space-y-1.5 ${className}`}>
      <button
        type="button"
        className="btn btn-ghost px-2.5 py-1 text-xs"
        aria-expanded={open}
        disabled={items.length === 0}
        title={items.length === 0 ? emptyHint : undefined}
        onClick={() => {
          setOpen((o) => !o)
          setDone(null)
        }}
      >
        <ListPlus className="mr-1 h-3.5 w-3.5" />
        Add to playlist
      </button>
      {open && (
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="input py-1 text-xs"
            aria-label="Playlist"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          >
            {playlists.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.item_count})
              </option>
            ))}
            <option value={NEW}>New playlist…</option>
          </select>
          {target === NEW && (
            <input
              className="input py-1 text-xs"
              aria-label="New playlist name"
              placeholder="Name"
              value={name}
              maxLength={255}
              onChange={(e) => setName(e.target.value)}
            />
          )}
          <button
            type="button"
            className="btn btn-primary px-2.5 py-1 text-xs"
            disabled={!ready || busy}
            onClick={() => void add()}
          >
            Add {items.length === 1 ? 'it' : `${items.length}`}
          </button>
        </div>
      )}
      {done && (
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Added to{' '}
          <Link
            to={`/playlists/${done.id}`}
            className="text-blue-700 hover:underline dark:text-blue-300"
          >
            {done.name}
          </Link>
          .
        </p>
      )}
      {problem && (
        <p role="alert" className="text-xs text-red-700 dark:text-red-400">
          {problem}
        </p>
      )}
    </div>
  )
}
