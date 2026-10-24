import { useMemo, useState } from 'react'
import type { Tag } from '@/api/tags'

/**
 * An autocomplete for the tag *filter*.
 *
 * This replaced a row of chips cut off at the first dozen tags, which made every tag
 * past the cut unfilterable-by-click. It differs from `TagInput` on purpose: that one
 * creates tags by name, while a filter on a name that does not exist can only ever show
 * an empty grid — so here Enter picks from the menu and free text is never committed.
 * Chosen tags are shown by FilterBar's active-chip row, so they are only hidden from
 * the menu rather than rendered again here.
 */

interface Props {
  tags: Tag[]
  /** Names currently filtered on; compared case-insensitively, as the server does. */
  selected: string[]
  onPick: (name: string) => void
}

// The menu scrolls, so this is a guard against rendering thousands of rows, not the
// limit the old chip row was.
const MAX_OPTIONS = 50

const LISTBOX_ID = 'tag-filter-suggestions'

export default function TagFilterInput({ tags, selected, onPick }: Props) {
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  const matches = useMemo(() => {
    const term = draft.trim().toLowerCase()
    const chosen = new Set(selected.map((name) => name.toLowerCase()))
    return tags
      .filter((tag) => !chosen.has(tag.name.toLowerCase()))
      .filter((tag) => !term || tag.name.toLowerCase().includes(term))
      .slice(0, MAX_OPTIONS)
  }, [tags, selected, draft])

  const pick = (name: string) => {
    onPick(name)
    setDraft('')
    setActive(0)
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setOpen(true)
      setActive((i) => Math.min(i + 1, matches.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const match = matches[active]
      if (open && match) pick(match.name)
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="relative w-64 max-w-full">
      <input
        role="combobox"
        aria-expanded={open && matches.length > 0}
        aria-controls={LISTBOX_ID}
        aria-autocomplete="list"
        aria-label="Filter by tag"
        className="input py-1 text-xs"
        placeholder="Filter by tag…"
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value)
          setOpen(true)
          setActive(0)
        }}
        onFocus={() => setOpen(true)}
        // A timeout, not a plain close: clicking an option blurs the input first, and
        // unmounting the menu synchronously would eat the click.
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={onKeyDown}
      />

      {open && matches.length > 0 && (
        <ul
          id={LISTBOX_ID}
          role="listbox"
          className="absolute left-0 top-full z-20 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
        >
          {matches.map((tag, index) => (
            <li key={tag.id}>
              <button
                type="button"
                role="option"
                aria-selected={index === active}
                onMouseEnter={() => setActive(index)}
                onClick={() => pick(tag.name)}
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
  )
}
