import { Check } from 'lucide-react'
import { formatDuration } from '@/utils/format'
import AssetThumb from '@/components/AssetThumb'
import type { Asset } from '@/api/assets'

interface Props {
  asset: Asset
  /** The click, modifiers included — the grid decides whether it opens or selects. */
  onActivate: (asset: Asset, event: React.MouseEvent) => void
  selected?: boolean
}

export default function AssetCard({ asset, onActivate, selected = false }: Props) {
  const duration = formatDuration(asset.duration_seconds)

  return (
    <button
      type="button"
      onClick={(event) => onActivate(asset, event)}
      // A real checkbox would have to live inside this button, which is invalid markup
      // and read unpredictably; `aria-pressed` says the same thing about the one
      // control that is actually here.
      aria-pressed={selected}
      // max-w is what actually caps a tile. The grid's auto-fill tracks stretch to
      // roughly twice their minimum before a further column fits, which at the widest
      // step would be ~440px; the grid's `justify-items-center` keeps a capped tile
      // centred in its track rather than leaving the gutter all on one side.
      className={`card group flex w-full max-w-[25rem] flex-col overflow-hidden p-0 text-left ${
        selected ? 'ring-2 ring-blue-600 dark:ring-blue-400' : ''
      }`}
    >
      <div className="relative aspect-[4/3] w-full overflow-hidden bg-gray-100 dark:bg-gray-800">
        <AssetThumb
          asset={asset}
          className="h-full w-full transition-transform duration-200 group-hover:scale-[1.03]"
        />
        {selected && (
          <span
            aria-hidden
            className="absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-blue-600 text-white shadow"
          >
            <Check className="h-3.5 w-3.5" />
          </span>
        )}
        {duration && (
          <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">
            {duration}
          </span>
        )}
      </div>

      <div className="min-w-0 px-2.5 py-2">
        {/* break-words, not truncate: a filename is often distinguished by its tail,
            and "IMG_2024_beach_sun…" tells you nothing. */}
        <p className="line-clamp-2 break-words text-xs font-medium text-gray-900 dark:text-gray-100">
          {asset.name}
        </p>
      </div>
    </button>
  )
}
