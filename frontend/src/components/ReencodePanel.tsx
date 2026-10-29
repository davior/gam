import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Minimize2 } from 'lucide-react'
import type { Asset } from '@/api/assets'
import { REENCODE_TARGETS, clipsApi, reencodeLabel } from '@/api/clips'
import type { ActivityJob } from '@/api/transcripts'
import { useLibraryStore } from '@/stores/library'
import EnrichmentButton from '@/components/EnrichmentButton'

/**
 * Make a smaller copy of a video, as a new asset: a 4K original that is too heavy for a
 * 1080p Video Creator project.
 *
 * Only sizes below the video's own are offered, the same rule the server enforces —
 * scaling up adds bytes and no detail. The copy carries the original's tags,
 * attribution and transcript, so the original can be deleted once the copy is checked;
 * nothing here deletes it, since that is the one step that cannot be undone.
 */

interface Props {
  asset: Asset
}

export default function ReencodePanel({ asset }: Props) {
  const shortSide =
    asset.width && asset.height ? Math.min(asset.width, asset.height) : null
  const targets = REENCODE_TARGETS.filter((t) => shortSide === null || t < shortSide)
  const preferred = targets.includes(1080) ? 1080 : targets[0]
  const [target, setTarget] = useState<number | undefined>(preferred)
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null)
  const openById = useLibraryStore((s) => s.openById)

  // Re-seed when a different asset opens in the same panel.
  useEffect(() => {
    setTarget(preferred)
    setCreated(null)
  }, [asset.id, preferred])

  if (target === undefined) return null

  const onFinish = (job: ActivityJob) => {
    if (job.status !== 'done' || !job.result_asset_id) return
    // Into the store, so the copy has a card in the grid before the library reloads.
    void openById(job.result_asset_id).then((fresh) => {
      setCreated({ id: fresh.id, name: fresh.name })
    })
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <label
          htmlFor={`reencode-${asset.id}`}
          className="shrink-0 text-xs text-gray-600 dark:text-gray-400"
        >
          Smaller copy
        </label>
        <select
          id={`reencode-${asset.id}`}
          className="input py-1 text-xs"
          value={target}
          onChange={(e) => setTarget(Number(e.target.value))}
        >
          {targets.map((t) => (
            <option key={t} value={t}>
              {reencodeLabel(t)}
            </option>
          ))}
        </select>
      </div>
      <EnrichmentButton
        assetId={asset.id}
        action="reencode"
        icon={Minimize2}
        label={`Re-encode to ${reencodeLabel(target)}`}
        runningLabel="Re-encoding…"
        start={(id) => clipsApi.reencode(id, target)}
        failureMessage="Could not start re-encoding"
        refreshOnFinish={false}
        onFinish={onFinish}
      />
      {created && (
        <p className="text-xs text-green-700 dark:text-green-400">
          Created &ldquo;{created.name}&rdquo; —{' '}
          <Link to={`/a/${created.id}`} className="underline">
            view it
          </Link>
          . Its tags, credit and transcript came with it, so this original can be deleted
          once you have checked it.
        </p>
      )}
    </div>
  )
}
