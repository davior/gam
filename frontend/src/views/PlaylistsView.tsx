import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ListVideo, Plus, Trash2 } from 'lucide-react'
import { apiErrorMessage } from '@/api/client'
import { usePlaylistStore } from '@/stores/playlists'
import { formatDuration } from '@/utils/format'
import { useReadOnly } from '@/utils/useReadOnly'

/** Every playlist, newest edit first, and a box to start a new one. */
export default function PlaylistsView() {
  const playlists = usePlaylistStore((s) => s.playlists)
  const loaded = usePlaylistStore((s) => s.loaded)
  const error = usePlaylistStore((s) => s.error)
  const load = usePlaylistStore((s) => s.load)
  const create = usePlaylistStore((s) => s.create)
  const remove = usePlaylistStore((s) => s.remove)
  const readOnly = useReadOnly()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  // Always fetched on arrival: the assistant may have made one since the list was read.
  useEffect(() => {
    void load()
  }, [load])

  async function submit(e: FormEvent) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    try {
      const made = await create(trimmed)
      setName('')
      navigate(`/playlists/${made.id}`)
    } catch (err) {
      setProblem(apiErrorMessage(err, 'Could not make the playlist'))
    }
  }

  async function drop(id: string, title: string) {
    if (
      !window.confirm(`Delete the playlist "${title}"? The assets in it are not deleted.`)
    )
      return
    try {
      await remove(id)
    } catch (err) {
      setProblem(apiErrorMessage(err, 'Could not delete the playlist'))
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 overflow-y-auto p-4">
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        Playlists
      </h2>
      {!readOnly && (
        <form onSubmit={submit} className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="New playlist name"
            aria-label="New playlist name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={255}
          />
          <button type="submit" className="btn btn-primary px-3" disabled={!name.trim()}>
            <Plus className="h-4 w-4" /> Create
          </button>
        </form>
      )}
      {(problem || error) && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {problem || error}
        </p>
      )}
      {loaded && playlists.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No playlists yet. Make one here, add to one from the library's selection bar, or
          ask the assistant for one ("a playlist of everyone talking about ...").
        </p>
      )}
      <ul className="divide-y divide-gray-200 rounded border border-gray-200 dark:divide-gray-700 dark:border-gray-700">
        {playlists.map((p) => (
          <li key={p.id} className="flex items-center gap-3 p-3">
            <div className="flex h-12 w-20 shrink-0 items-center justify-center overflow-hidden rounded bg-gray-100 dark:bg-gray-800">
              {p.thumb_url ? (
                <img src={p.thumb_url} alt="" className="h-full w-full object-cover" />
              ) : (
                <ListVideo className="h-5 w-5 text-gray-400" aria-hidden />
              )}
            </div>
            <Link to={`/playlists/${p.id}`} className="min-w-0 flex-1 hover:underline">
              <span className="block truncate font-medium text-gray-900 dark:text-gray-100">
                {p.name}
              </span>
              <span className="block text-xs text-gray-500 dark:text-gray-400">
                {p.item_count} {p.item_count === 1 ? 'item' : 'items'}
                {p.total_seconds > 0 ? ` · ${formatDuration(p.total_seconds)}` : ''}
              </span>
            </Link>
            {!readOnly && (
              <button
                type="button"
                className="btn btn-ghost p-2"
                onClick={() => void drop(p.id, p.name)}
                aria-label={`Delete ${p.name}`}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
