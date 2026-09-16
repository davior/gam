import type { AssetType } from '@/api/assets'

/**
 * The asset-type filter, as a row of chips.
 *
 * Lifted out of `FilterBar` so search can have one too. `FilterBar` itself is not
 * reusable here: it is wired to `useLibraryStore` for all nine of its filters, and
 * search shares exactly one of them.
 */

const TYPE_FILTERS: Array<{ value: AssetType | null; label: string }> = [
  { value: null, label: 'All' },
  { value: 'video', label: 'Video' },
  { value: 'image', label: 'Images' },
  { value: 'audio', label: 'Audio' },
  { value: 'document', label: 'Documents' },
]

export function Chip({
  active,
  onClick,
  children,
}: {
  active?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? 'bg-blue-600 text-white'
          : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'
      }`}
    >
      {children}
    </button>
  )
}

interface Props {
  value: AssetType | null
  onChange: (value: AssetType | null) => void
}

export default function TypeFilterChips({ value, onChange }: Props) {
  return (
    <div className="flex flex-wrap gap-1">
      {TYPE_FILTERS.map((filter) => (
        <Chip
          key={filter.label}
          active={value === filter.value}
          onClick={() => onChange(filter.value)}
        >
          {filter.label}
        </Chip>
      ))}
    </div>
  )
}
