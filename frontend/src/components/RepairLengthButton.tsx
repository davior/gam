import { Wrench } from 'lucide-react'
import type { Asset } from '@/api/assets'
import { needsLengthRepair, versionsApi } from '@/api/versions'
import EnrichmentButton from '@/components/EnrichmentButton'

/**
 * Offered on a recording whose file never said how long it is.
 *
 * A browser writes a recording as a live stream, with no duration and no index, so the
 * library has no length for it: the seek bar cannot seek and Video Creator cannot place
 * the clip. New uploads are measured on arrival; this repairs the ones that were already
 * here. It rewrites the container with the same picture and sound (nothing is
 * re-encoded) behind the same asset, so tags, clips, playlists and projects carry on.
 *
 * Shown only while the length is missing, which is also the signal that it worked: the
 * button goes away once the asset has one.
 */

export default function RepairLengthButton({ asset }: { asset: Asset }) {
  if (!needsLengthRepair(asset)) return null
  return (
    <div className="space-y-1 rounded-md border border-amber-200 p-3 dark:border-amber-900">
      <p className="text-xs text-gray-700 dark:text-gray-300">
        This recording has no length stored, so it can’t be seeked and Video Creator can’t
        place it. Repairing rewrites the file with the same picture and sound; nothing is
        re-encoded.
      </p>
      <EnrichmentButton
        assetId={asset.id}
        action="remux"
        icon={Wrench}
        label="Repair length"
        runningLabel="Repairing…"
        start={versionsApi.remux}
        failureMessage="Could not start the repair"
      />
    </div>
  )
}
