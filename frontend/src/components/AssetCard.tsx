import { formatDuration } from '@/utils/format'
import AssetThumb from '@/components/AssetThumb'
import type { Asset } from '@/api/assets'

interface Props {
  asset: Asset
  onOpen: (asset: Asset) => void
}

export default function AssetCard({ asset, onOpen }: Props) {
  const duration = formatDuration(asset.duration_seconds)

  return (
    <button
      type="button"
      onClick={() => onOpen(asset)}
      className="card group flex flex-col overflow-hidden p-0 text-left"
    >
      <div className="relative aspect-[4/3] w-full overflow-hidden bg-gray-100 dark:bg-gray-800">
        <AssetThumb
          asset={asset}
          className="h-full w-full transition-transform duration-200 group-hover:scale-[1.03]"
        />
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
