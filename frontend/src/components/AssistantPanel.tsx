import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ExternalLink, Loader2, Mic, Plus, Send, Square, Trash2, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import Markdown from '@/components/Markdown'
import {
  isActiveTurn,
  type ApplyResult,
  type AssistantTurn,
  type Proposal,
  type ProposalClip,
  type ProposalNote,
  type ProposalPlaylist,
  type ProposalItem,
} from '@/api/assistant'
import { currentConfig, loadConfig } from '@/api/config'
import { suiteSpeechApi, type DictationReadiness } from '@/api/suiteSpeech'
import { useDictation } from '@/hooks/useDictation'
import { formatDuration } from '@/utils/format'
import { useAssistantStore } from '@/stores/assistant'

/**
 * The assistant, beside whatever the person is looking at.
 *
 * It asks, it looks, and it proposes. It never edits: what it wants changed arrives as a
 * card that says what would be different, and nothing happens until Apply. A discarded
 * card costs nothing, which is what makes it safe to let a model propose freely.
 */

const STEP_LABEL: Record<string, string> = {
  search: 'Searching',
  read: 'Reading',
  propose: 'Proposing',
  think: 'Thinking',
  issue: 'Fixing',
}

export default function AssistantPanel() {
  const {
    status,
    sessions,
    personas,
    personaId,
    active,
    focus,
    sending,
    error,
    applied,
  } = useAssistantStore()
  const send = useAssistantStore((s) => s.send)
  const stop = useAssistantStore((s) => s.stop)
  const setPersona = useAssistantStore((s) => s.setPersona)
  const select = useAssistantStore((s) => s.select)
  const newChat = useAssistantStore((s) => s.newChat)
  const remove = useAssistantStore((s) => s.remove)
  const close = useAssistantStore((s) => s.setOpen)
  const [text, setText] = useState('')
  // Dictation: each finished phrase is appended to what is typed; nothing is sent until the
  // person presses Send. Offered when the suite can transcribe, and explained when it cannot.
  const [dictationReady, setDictationReady] = useState<DictationReadiness>({
    state: 'local',
  })
  const [showWhyNot, setShowWhyNot] = useState(false)
  const dictation = useDictation((phrase) =>
    setText((prev) => (prev.trim() ? `${prev.trimEnd()} ${phrase}` : phrase))
  )
  useEffect(() => {
    let live = true
    suiteSpeechApi
      .ready()
      .then((ready) => live && setDictationReady(ready))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [])
  const [suiteUrl, setSuiteUrl] = useState(() => currentConfig()?.suite_base_url ?? '')
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let stale = false
    loadConfig()
      .then((config) => {
        if (!stale) setSuiteUrl(config.suite_base_url)
      })
      .catch(() => {})
    return () => {
      stale = true
    }
  }, [])

  const turns = active?.turns ?? []
  const running = turns.some(isActiveTurn)
  const last = turns[turns.length - 1]
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [turns.length, last?.steps.length, last?.status])

  const canType = status?.ready === true
  function submit() {
    if (!canType || running || sending || !text.trim()) return
    // Sending ends a dictation: what was said so far is what is sent.
    if (dictation.status === 'recording') dictation.stop()
    void send(text)
    setText('')
  }
  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <aside
      aria-label="Assistant"
      className="fixed inset-y-0 right-0 z-40 flex w-full flex-col border-l border-gray-200 bg-white sm:static sm:z-auto sm:w-96 sm:shrink-0 dark:border-gray-700 dark:bg-gray-800"
    >
      <header className="flex shrink-0 items-center gap-2 border-b border-gray-200 px-3 py-2 dark:border-gray-700">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Assistant
        </h2>
        {status?.model && (
          <span className="truncate text-xs text-gray-500 dark:text-gray-400">
            {status.model}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            className="btn btn-ghost p-1.5"
            onClick={newChat}
            aria-label="New conversation"
            title="New conversation"
          >
            <Plus className="h-4 w-4" />
          </button>
          {active && (
            <button
              type="button"
              className="btn btn-ghost p-1.5"
              onClick={() => void remove(active.id)}
              aria-label="Delete this conversation"
              title="Delete this conversation"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
          <button
            type="button"
            className="btn btn-ghost p-1.5"
            onClick={() => close(false)}
            aria-label="Close assistant"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>

      {sessions.length > 0 && (
        <div className="shrink-0 border-b border-gray-200 px-3 py-2 dark:border-gray-700">
          <select
            aria-label="Conversation"
            className="input w-full text-sm"
            value={active?.id ?? ''}
            onChange={(event) => {
              if (event.target.value) void select(event.target.value)
              else newChat()
            }}
          >
            <option value="">New conversation</option>
            {sessions.map((session) => (
              <option key={session.id} value={session.id}>
                {session.title || 'Untitled'}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="flex-1 space-y-4 overflow-y-auto px-3 py-3 text-sm">
        {status && !status.available && <Unavailable message={status.message} />}
        {status?.available && !status.ready && (
          <div
            role="status"
            className="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
          >
            <p>{status.message}</p>
            {suiteUrl && (
              <a
                href={`${suiteUrl.replace(/\/$/, '')}/routing`}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-2 inline-flex items-center gap-1 underline"
              >
                Open Gecko Suite
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
        )}
        {status?.ready && turns.length === 0 && (
          <p className="text-gray-500 dark:text-gray-400">
            Ask about what is in the library, or have it tidy names, descriptions and
            tags. It proposes; you decide what to apply.
          </p>
        )}
        {turns.map((turn) => (
          <Turn key={turn.id} turn={turn} applied={applied} />
        ))}
        {error && (
          <p role="alert" className="text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
        <div ref={bottom} />
      </div>

      <footer className="shrink-0 border-t border-gray-200 p-3 dark:border-gray-700">
        {focus && (
          <p className="mb-2 truncate text-xs text-gray-500 dark:text-gray-400">
            About: <span className="font-medium">{focus.name}</span>
          </p>
        )}
        {personas.length > 0 && (
          <select
            aria-label="Persona"
            className="input mb-2 w-full text-xs"
            value={personaId ?? ''}
            title={personas.find((p) => p.id === personaId)?.description || undefined}
            onChange={(event) => setPersona(event.target.value || null)}
          >
            <option value="">Assistant (no persona)</option>
            {personas.map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.name}
              </option>
            ))}
          </select>
        )}
        <div className="flex items-end gap-2">
          <textarea
            aria-label="Message"
            rows={2}
            className="input min-h-0 flex-1 resize-none text-sm"
            placeholder={
              canType ? 'Ask the assistant…' : 'The assistant is not available'
            }
            value={text}
            disabled={!canType}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={onKey}
          />
          {canType && dictationReady.state === 'unavailable' && (
            // Not `disabled`: a disabled button takes no click, and the click is how the
            // person finds out why (a title alone does not show on touch screens).
            <button
              type="button"
              className="btn btn-secondary p-2 opacity-50"
              aria-label="Dictate (unavailable)"
              aria-disabled="true"
              title={dictationReady.reason}
              onClick={() => setShowWhyNot((v) => !v)}
            >
              <Mic className="h-4 w-4" />
            </button>
          )}
          {canType && dictationReady.state === 'ready' && (
            <button
              type="button"
              className={`btn p-2 ${
                dictation.status === 'recording'
                  ? 'btn-danger animate-pulse'
                  : 'btn-secondary'
              }`}
              aria-label={dictation.status === 'recording' ? 'Stop dictating' : 'Dictate'}
              aria-pressed={dictation.status === 'recording'}
              title={dictation.status === 'recording' ? 'Stop dictating' : 'Dictate'}
              disabled={dictation.status === 'finishing'}
              onClick={dictation.toggle}
            >
              {dictation.status === 'finishing' ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Mic className="h-4 w-4" />
              )}
            </button>
          )}
          {running ? (
            <button
              type="button"
              className="btn btn-secondary p-2"
              onClick={() => void stop()}
              aria-label="Stop"
              title="Stop"
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary p-2"
              onClick={submit}
              disabled={!canType || sending || !text.trim()}
              aria-label="Send"
              title="Send"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
        {dictation.interim && (
          <p
            className="mt-1 truncate text-xs italic text-gray-500 dark:text-gray-400"
            aria-live="polite"
          >
            {dictation.interim}…
          </p>
        )}
        {showWhyNot && dictationReady.state === 'unavailable' && (
          <p role="alert" className="mt-1 text-xs text-amber-700 dark:text-amber-400">
            Dictation is off: {dictationReady.reason}
          </p>
        )}
        {dictation.status === 'error' && dictation.error && (
          <p role="alert" className="mt-1 text-xs text-red-600 dark:text-red-400">
            {dictation.error}
          </p>
        )}
      </footer>
    </aside>
  )
}

function Unavailable({ message }: { message: string | null }) {
  return (
    <div
      role="status"
      className="rounded border border-gray-200 bg-gray-50 p-3 text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
    >
      {message ?? 'The assistant is not available.'}
    </div>
  )
}

function Turn({ turn, applied }: { turn: AssistantTurn; applied: ApplyResult[] | null }) {
  const active = isActiveTurn(turn)
  return (
    <section className="space-y-2" aria-label="Exchange">
      <p className="ml-6 whitespace-pre-wrap rounded bg-blue-50 px-3 py-2 text-gray-900 dark:bg-blue-950 dark:text-gray-100">
        {turn.prompt}
      </p>

      {turn.steps.length > 0 && (
        <ul className="space-y-0.5 text-xs text-gray-500 dark:text-gray-400">
          {(active ? turn.steps.slice(-4) : turn.steps).map((step, index) => (
            <li key={index} className="truncate">
              <span className="font-medium">{STEP_LABEL[step.kind] ?? step.kind}</span>
              {': '}
              {step.text}
            </li>
          ))}
        </ul>
      )}
      {active && (
        <p className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
          <Loader2 className="h-3 w-3 animate-spin" />
          {turn.detail ? `Working: ${turn.detail}` : 'Thinking…'}
        </p>
      )}
      {turn.status === 'error' && (
        <p role="alert" className="text-red-600 dark:text-red-400">
          {turn.error ?? 'Something went wrong.'}
        </p>
      )}
      {turn.status === 'cancelled' && (
        <p className="text-gray-500 dark:text-gray-400">Stopped.</p>
      )}
      {turn.status === 'done' && turn.reply && <Markdown text={turn.reply} />}
      {turn.proposal && turn.outcome && (
        <ProposalCard turn={turn} proposal={turn.proposal} applied={applied} />
      )}
    </section>
  )
}

function ProposalCard({
  turn,
  proposal,
  applied,
}: {
  turn: AssistantTurn
  proposal: Proposal
  applied: ApplyResult[] | null
}) {
  const apply = useAssistantStore((s) => s.apply)
  const discard = useAssistantStore((s) => s.discard)
  const items = proposal.items
  const clips = proposal.clips ?? []
  const playlists = proposal.playlists ?? []
  const notes = proposal.notes ?? []
  // Ticked off by id: an asset's id for its edits, a clip's own id for a clip or a cut,
  // a playlist's ref for a playlist, a note's for a note.
  const [skipped, setSkipped] = useState<Set<string>>(new Set())
  const chosen = items.filter((item) => !skipped.has(item.asset_id))
  const chosenClips = clips.filter((clip) => !skipped.has(clip.clip_id))
  const chosenPlaylists = playlists.filter((p) => !skipped.has(p.playlist_ref))
  const chosenNotes = notes.filter((n) => !skipped.has(n.note_ref))
  const total = items.length + clips.length + playlists.length + notes.length
  const picked =
    chosen.length + chosenClips.length + chosenPlaylists.length + chosenNotes.length
  const pending = turn.outcome === 'pending'

  function toggle(id: string) {
    setSkipped((now) => {
      const next = new Set(now)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div
      aria-label="Proposed changes"
      className="space-y-3 rounded border border-gray-200 p-3 dark:border-gray-700"
    >
      <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
        {pending
          ? 'Proposed changes'
          : turn.outcome === 'applied'
            ? 'Applied'
            : 'Discarded'}
      </h3>
      <ul className="space-y-3">
        {items.map((item) => (
          <li key={item.asset_id} className="space-y-1">
            <div className="flex items-center gap-2">
              {pending && (
                <input
                  type="checkbox"
                  aria-label={`Include ${item.asset_name}`}
                  checked={!skipped.has(item.asset_id)}
                  onChange={() => toggle(item.asset_id)}
                />
              )}
              <Link
                to={`/a/${item.asset_id}`}
                className="truncate font-medium text-blue-700 hover:underline dark:text-blue-300"
              >
                {item.before.name}
              </Link>
            </div>
            <Changes item={item} />
            {!pending && turn.outcome === 'applied' && (
              <Outcome
                result={applied?.find((r) => !r.clip_id && r.asset_id === item.asset_id)}
              />
            )}
          </li>
        ))}
        {clips.map((clip) => (
          <ClipRow
            key={clip.clip_id}
            clip={clip}
            pending={pending}
            included={!skipped.has(clip.clip_id)}
            onToggle={() => toggle(clip.clip_id)}
            result={
              !pending && turn.outcome === 'applied'
                ? applied?.find((r) => r.clip_id === clip.clip_id)
                : undefined
            }
          />
        ))}
        {playlists.map((playlist) => (
          <PlaylistRow
            key={playlist.playlist_ref}
            playlist={playlist}
            pending={pending}
            included={!skipped.has(playlist.playlist_ref)}
            onToggle={() => toggle(playlist.playlist_ref)}
            result={
              !pending && turn.outcome === 'applied'
                ? applied?.find((r) => r.playlist_ref === playlist.playlist_ref)
                : undefined
            }
          />
        ))}
        {notes.map((note) => (
          <NoteRow
            key={note.note_ref}
            note={note}
            pending={pending}
            included={!skipped.has(note.note_ref)}
            onToggle={() => toggle(note.note_ref)}
            result={
              !pending && turn.outcome === 'applied'
                ? applied?.find((r) => r.note_ref === note.note_ref)
                : undefined
            }
          />
        ))}
      </ul>
      {pending && (
        <div className="flex gap-2">
          <button
            type="button"
            className="btn btn-primary px-3 py-1"
            disabled={picked === 0}
            onClick={() =>
              void apply(
                turn.id,
                picked === total
                  ? undefined
                  : {
                      assetIds: chosen.map((c) => c.asset_id),
                      clipIds: chosenClips.map((c) => c.clip_id),
                      playlistRefs: chosenPlaylists.map((p) => p.playlist_ref),
                      noteRefs: chosenNotes.map((n) => n.note_ref),
                    }
              )
            }
          >
            Apply {picked === total ? 'all' : `${picked} of ${total}`}
          </button>
          <button
            type="button"
            className="btn btn-secondary px-3 py-1"
            onClick={() => void discard(turn.id)}
          >
            Discard
          </button>
        </div>
      )}
    </div>
  )
}

/** One clip or cut: what it is, where it comes from, and why this stretch. */
function ClipRow({
  clip,
  pending,
  included,
  onToggle,
  result,
}: {
  clip: ProposalClip
  pending: boolean
  included: boolean
  onToggle: () => void
  result?: ApplyResult
}) {
  const title = clip.name || `Clip of ${clip.asset_name}`
  const length = Math.round(clip.out_point - clip.in_point)
  return (
    <li className="space-y-1">
      <div className="flex items-center gap-2">
        {pending && (
          <input
            type="checkbox"
            aria-label={`Include ${title}`}
            checked={included}
            onChange={onToggle}
          />
        )}
        <span className="shrink-0 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-blue-800 dark:bg-blue-900/40 dark:text-blue-200">
          {clip.kind === 'cut' ? 'New video' : 'Clip'}
        </span>
        <span className="truncate font-medium text-gray-900 dark:text-gray-100">
          {title}
        </span>
      </div>
      <p className="text-xs text-gray-600 dark:text-gray-400">
        From{' '}
        <Link
          to={`/a/${clip.asset_id}`}
          className="text-blue-700 hover:underline dark:text-blue-300"
        >
          {clip.asset_name}
        </Link>
        , {formatDuration(clip.in_point)}–{formatDuration(clip.out_point)} ({length}{' '}
        {length === 1 ? 'second' : 'seconds'})
      </p>
      {clip.reason && (
        <p className="text-xs italic text-gray-600 dark:text-gray-400">{clip.reason}</p>
      )}
      {result && <ClipOutcome result={result} />}
    </li>
  )
}

function ClipOutcome({ result }: { result: ApplyResult }) {
  return (
    <p className="text-xs text-gray-500 dark:text-gray-400">
      {result.applied.includes('clip made') && result.created_asset_id && (
        <>
          Clip made.{' '}
          <Link
            to={`/a/${result.created_asset_id}`}
            className="text-blue-700 hover:underline dark:text-blue-300"
          >
            Open it
          </Link>
          .{' '}
        </>
      )}
      {result.applied.includes('cut queued') &&
        'Cut queued. The new video appears in the library when it has been made (see Background activity). '}
      {result.skipped.join(' ')}
    </p>
  )
}

/** A proposed playlist: new or added to, and each moment in it with why it was chosen. */
function PlaylistRow({
  playlist,
  pending,
  included,
  onToggle,
  result,
}: {
  playlist: ProposalPlaylist
  pending: boolean
  included: boolean
  onToggle: () => void
  result?: ApplyResult
}) {
  const count = playlist.items.length
  return (
    <li className="space-y-1">
      <div className="flex items-center gap-2">
        {pending && (
          <input
            type="checkbox"
            aria-label={`Include ${playlist.name}`}
            checked={included}
            onChange={onToggle}
          />
        )}
        <span className="shrink-0 rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-purple-800 dark:bg-purple-900/40 dark:text-purple-200">
          {playlist.playlist_id ? 'Add to playlist' : 'New playlist'}
        </span>
        <span className="truncate font-medium text-gray-900 dark:text-gray-100">
          {playlist.name}
        </span>
        <span className="shrink-0 text-xs text-gray-500 dark:text-gray-400">
          {count} {count === 1 ? 'item' : 'items'}
        </span>
      </div>
      {playlist.description && (
        <p className="text-xs text-gray-600 dark:text-gray-400">{playlist.description}</p>
      )}
      <ol className="ml-5 list-decimal space-y-1 text-xs text-gray-700 dark:text-gray-300">
        {playlist.items.map((item, at) => (
          <li key={`${item.asset_id}-${item.in_point ?? 'whole'}-${at}`}>
            <span className="font-medium">{item.label || item.asset_name}</span>
            {' — '}
            <Link
              to={`/a/${item.asset_id}`}
              className="text-blue-700 hover:underline dark:text-blue-300"
            >
              {item.asset_name}
            </Link>
            {item.in_point !== null && item.out_point !== null
              ? `, ${formatDuration(item.in_point)}–${formatDuration(item.out_point)}`
              : item.asset_type === 'image'
                ? ''
                : ', whole'}
            {item.reason && (
              <span className="block italic text-gray-500 dark:text-gray-400">
                {item.reason}
              </span>
            )}
          </li>
        ))}
      </ol>
      {result && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          {result.playlist_id && (
            <>
              {result.applied.includes('playlist made') ? 'Playlist made.' : 'Added.'}{' '}
              <Link
                to={`/playlists/${result.playlist_id}`}
                className="text-blue-700 hover:underline dark:text-blue-300"
              >
                Open it
              </Link>
              .{' '}
            </>
          )}
          {result.skipped.join(' ')}
        </p>
      )}
    </li>
  )
}

/** A note for Gecko Notes: its title and folder, and the text, to read before applying. */
function NoteRow({
  note,
  pending,
  included,
  onToggle,
  result,
}: {
  note: ProposalNote
  pending: boolean
  included: boolean
  onToggle: () => void
  result?: ApplyResult
}) {
  const [open, setOpen] = useState(false)
  return (
    <li className="space-y-1">
      <div className="flex items-center gap-2">
        {pending && (
          <input
            type="checkbox"
            aria-label={`Include ${note.title}`}
            checked={included}
            onChange={onToggle}
          />
        )}
        <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
          New note
        </span>
        <span className="truncate font-medium text-gray-900 dark:text-gray-100">
          {note.title}
        </span>
      </div>
      <p className="text-xs text-gray-600 dark:text-gray-400">
        In Gecko Notes, folder “{note.folder || 'From GAM'}”
        {note.asset_ids.length > 0 &&
          `, linking ${note.asset_ids.length} ${note.asset_ids.length === 1 ? 'asset' : 'assets'}`}
        .{' '}
        <button
          type="button"
          className="text-blue-700 hover:underline dark:text-blue-300"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? 'Hide the text' : 'Read it'}
        </button>
      </p>
      {open && (
        <div
          aria-label={`Text of ${note.title}`}
          className="max-h-72 overflow-auto rounded border border-gray-200 p-2 text-xs dark:border-gray-700"
        >
          <Markdown text={note.markdown} />
        </div>
      )}
      {result && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          {result.note_url && (
            <>
              Note created.{' '}
              <a
                href={result.note_url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-700 hover:underline dark:text-blue-300"
              >
                Open in Gecko Notes
              </a>
              .{' '}
            </>
          )}
          {result.skipped.join(' ')}
        </p>
      )}
    </li>
  )
}

function Changes({ item }: { item: ProposalItem }) {
  const rows: { label: string; from?: string | null; to: string }[] = []
  if (item.name) rows.push({ label: 'Name', from: item.before.name, to: item.name })
  if (item.description)
    rows.push({
      label: 'Description',
      from: item.before.description,
      to: item.description,
    })
  if (item.summary)
    rows.push({ label: 'Summary', from: item.before.summary, to: item.summary })
  return (
    <div className="space-y-1 text-xs">
      {rows.map((row) => (
        <div key={row.label}>
          <span className="font-medium text-gray-700 dark:text-gray-300">
            {row.label}
          </span>
          {row.from && (
            <p className="text-gray-500 line-through dark:text-gray-500">{row.from}</p>
          )}
          <p className="whitespace-pre-wrap text-gray-900 dark:text-gray-100">{row.to}</p>
        </div>
      ))}
      {(item.add_tags.length > 0 || item.remove_tags.length > 0) && (
        <p className="flex flex-wrap gap-1">
          {item.add_tags.map((tag) => (
            <span
              key={`+${tag}`}
              className="rounded bg-green-100 px-1.5 py-0.5 text-green-900 dark:bg-green-900 dark:text-green-100"
            >
              + {tag}
            </span>
          ))}
          {item.remove_tags.map((tag) => (
            <span
              key={`-${tag}`}
              className="rounded bg-red-100 px-1.5 py-0.5 text-red-900 dark:bg-red-900 dark:text-red-100"
            >
              − {tag}
            </span>
          ))}
        </p>
      )}
      {item.enrich.length > 0 && (
        <p className="text-gray-700 dark:text-gray-300">Run: {item.enrich.join(', ')}</p>
      )}
    </div>
  )
}

function Outcome({ result }: { result?: ApplyResult }) {
  if (!result) return null
  return (
    <p className="text-xs text-gray-500 dark:text-gray-400">
      {result.applied.length > 0 && `Applied: ${result.applied.join(', ')}. `}
      {result.skipped.join(' ')}
    </p>
  )
}
