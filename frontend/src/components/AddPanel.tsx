import { X } from 'lucide-react'
import UploadZone from '@/components/UploadZone'
import UrlImport from '@/components/UrlImport'

export const ADD_PANEL_ID = 'library-add-panel'

interface Props {
  open: boolean
  onClose: () => void
}

/**
 * The two ways into the library — files and links — folded behind the library's + button.
 *
 * Hidden rather than unmounted when closed. Unmounting would throw away a half-pasted
 * link and the import options with it, and would take an upload's progress bar off the
 * page while the upload itself carried on in the store; closing the panel is putting
 * it out of the way, not cancelling anything.
 *
 * The `hidden` attribute goes on an element with no display utility of its own:
 * Tailwind's `[hidden]` rule sits in the base layer, so a `flex` or `grid` class on the
 * same element would win and the panel would never close.
 */
export default function AddPanel({ open, onClose }: Props) {
  return (
    <section
      id={ADD_PANEL_ID}
      hidden={!open}
      aria-labelledby={`${ADD_PANEL_ID}-title`}
      className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2
          id={`${ADD_PANEL_ID}-title`}
          className="text-sm font-medium text-gray-900 dark:text-gray-100"
        >
          Add to library
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="btn btn-ghost p-1"
          // Named for what it closes: the asset panel beside the grid has a Close too,
          // and both can be on screen at once.
          aria-label="Close add panel"
          title="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="space-y-3">
        <UploadZone />
        <UrlImport />
      </div>
    </section>
  )
}
