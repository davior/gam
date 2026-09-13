import { useState } from 'react'
import { FileText, Film, ImageIcon, Music, TriangleAlert } from 'lucide-react'
import type { Asset } from '@/api/assets'

const ICONS = {
  image: ImageIcon,
  video: Film,
  audio: Music,
  document: FileText,
} as const

interface Props {
  asset: Asset
  className?: string
}

/**
 * An asset's preview, or an honest stand-in.
 *
 * Three distinct states, because they mean different things to the user: a real
 * thumbnail, a type icon (audio has no preview and never will), and a warning that the
 * file is gone. Collapsing the last two would present missing data as normal.
 */
export default function AssetThumb({ asset, className = '' }: Props) {
  const [failed, setFailed] = useState(false)
  const Icon = ICONS[asset.asset_type] ?? FileText

  if (asset.missing) {
    return (
      <div
        className={`flex items-center justify-center bg-amber-50 dark:bg-amber-950/30 ${className}`}
        title="This file is missing from storage"
      >
        <TriangleAlert className="h-6 w-6 text-amber-500" />
      </div>
    )
  }

  if (!asset.thumb_url || failed) {
    return (
      <div
        className={`flex items-center justify-center bg-gray-100 dark:bg-gray-800 ${className}`}
      >
        <Icon className="h-6 w-6 text-gray-400 dark:text-gray-500" />
      </div>
    )
  }

  return (
    <img
      src={asset.thumb_url}
      alt=""
      loading="lazy"
      // A signed URL can expire while the page is open. Falling back to the icon keeps
      // a stale tile looking deliberate rather than broken.
      onError={() => setFailed(true)}
      className={`object-cover ${className}`}
    />
  )
}
