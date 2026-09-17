import { useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import type { Tag } from '@/api/tags'

/**
 * A token input for tags.
 *
 * Commits by *name*, never by id, even when the user picked an existing tag from the
 * menu — the server does case-insensitive get-or-create, so a name is enough and
 * resolving it here first would turn one interaction into two round trips and a race
 * between two tabs creating the same tag.
 */

interface Props {
  /** Already attached. Rendered as removable tokens. */
  tags: Tag[]
  /** The whole catalogue, for the menu. Attached tags are filtered out of it. */
  suggestions: Tag[]
  onAdd: (names: string[]) => void
  onRemove: (tagId: string) => void
  disabled?: boolean
  placeholder?: string
  /** Rendered above the input; also the accessible name. */
  label?: string
  /** Rendered beside the label — the "suggest tags" AI trigger, so it sits at label
   *  height rather than as its own row below the input. */
  labelAdornment?: ReactNode
}

const MAX_SUGGESTIONS = 8

export default function TagInput({
  tags,
  suggestions,
  onAdd,
  onRemove,
  disabled = false,
  placeholder = 'Add a tag…',
  label = 'Tags',
  labelAdornment,
}: Props) {
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const attached = useMemo(() => new Set(tags.map((t) => t.name.toLowerCase())), [tags])

  const matches = useMemo(() => {
    const term = draft.trim().toLowerCase()
    return suggestions
      .filter((tag) => !attached.has(tag.name.toLowerCase()))
      .filter((tag) => !term || tag.name.toLowerCase().includes(term))
      .slice(0, MAX_SUGGESTIONS)
  }, [suggestions, attached, draft])

  const commit = (raw: string) => {
    // Collapse whitespace the way the server does, so what is sent is what comes back.
    const name = raw.trim().replace(/\s+/g, ' ')
    if (!name) return
    // Case-insensitive, because the server would fold "nato" onto an existing "NATO"
    // and hand back the same set — a request whose visible effect is nothing at all,
    // which reads as a broken control.
    if (attached.has(name.toLowerCase())) {
      setDraft('')
      return
    }
    onAdd([name])
    setDraft('')
    setActive(0)
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      const chosen = open && matches[active] ? matches[active].name : draft
      commit(chosen)
      return
    }
    if (event.key === 'Backspace' && draft === '' && tags.length > 0) {
      // Removing the last token is the expected escape hatch when the ✕ is a small
      // target and the hands are already on the keyboard.
      onRemove(tags[tags.length - 1].id)
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setOpen(true)
      setActive((i) => Math.min(i + 1, matches.length - 1))
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
      return
    }
    if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  const listboxId = 'tag-suggestions'

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <span className="label mb-0">{label}</span>
        {labelAdornment}
      </div>

      <div
        className="flex flex-wrap items-center gap-1 rounded-lg border border-gray-300 px-2 py-1.5 focus-within:ring-2 focus-within:ring-blue-500 dark:border-gray-600"
        onClick={() => inputRef.current?.focus()}
      >
        {tags.map((tag) => (
          <span
            key={tag.id}
            className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-900/50 dark:text-blue-200"
          >
            {tag.name}
            <button
              type="button"
              onClick={() => onRemove(tag.id)}
              disabled={disabled}
              className="rounded-full p-0.5 hover:bg-blue-200 dark:hover:bg-blue-800"
              aria-label={`Remove ${tag.name}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}

        <div className="relative min-w-[8rem] flex-1">
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded={open && matches.length > 0}
            aria-controls={listboxId}
            aria-autocomplete="list"
            aria-label={label}
            className="w-full bg-transparent text-xs outline-none placeholder:text-gray-400 dark:text-gray-100"
            placeholder={placeholder}
            value={draft}
            disabled={disabled}
            onChange={(e) => {
              setDraft(e.target.value)
              setOpen(true)
              setActive(0)
            }}
            onFocus={() => setOpen(true)}
            // A timeout, not a plain blur handler: clicking a suggestion blurs the
            // input first, and closing the menu synchronously would unmount the option
            // before its click ever lands.
            onBlur={() => window.setTimeout(() => setOpen(false), 120)}
            onKeyDown={onKeyDown}
          />

          {open && matches.length > 0 && (
            <ul
              id={listboxId}
              role="listbox"
              className="absolute left-0 top-full z-20 mt-1 max-h-52 w-56 overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
            >
              {matches.map((tag, index) => (
                <li key={tag.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={index === active}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => commit(tag.name)}
                    className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs ${
                      index === active
                        ? 'bg-blue-50 text-blue-900 dark:bg-blue-900/40 dark:text-blue-100'
                        : 'text-gray-700 dark:text-gray-200'
                    }`}
                  >
                    <span className="truncate">{tag.name}</span>
                    {tag.asset_count !== undefined && (
                      <span className="shrink-0 text-[11px] tabular-nums text-gray-400">
                        {tag.asset_count}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
