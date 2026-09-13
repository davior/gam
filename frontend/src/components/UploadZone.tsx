import { useCallback, useRef, useState, type DragEvent } from 'react'
import { Upload } from 'lucide-react'
import { useLibraryStore } from '@/stores/library'

/**
 * Drag-and-drop and click-to-browse.
 *
 * Drag events fire for every child element, so a plain enter/leave pair flickers as
 * the pointer crosses the grid inside. Counting enters and leaves is what makes the
 * highlight steady.
 */
export default function UploadZone() {
  const upload = useLibraryStore((s) => s.upload)
  const uploading = useLibraryStore((s) => s.uploading)
  const progress = useLibraryStore((s) => s.uploadProgress)

  const [dragDepth, setDragDepth] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const accept = useCallback(
    (fileList: FileList | null) => {
      const files = Array.from(fileList ?? [])
      if (files.length) void upload(files)
    },
    [upload]
  )

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragDepth(0)
    accept(event.dataTransfer?.files ?? null)
  }

  const active = dragDepth > 0

  return (
    <div
      onDragEnter={(e) => {
        e.preventDefault()
        setDragDepth((d) => d + 1)
      }}
      onDragLeave={(e) => {
        e.preventDefault()
        setDragDepth((d) => Math.max(0, d - 1))
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
      className={`rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors ${
        active
          ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/30'
          : 'border-gray-300 dark:border-gray-700'
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          accept(e.target.files)
          // Reset, or picking the same file twice in a row fires no change event.
          e.target.value = ''
        }}
      />

      {uploading ? (
        <div className="space-y-2">
          <p className="text-sm text-gray-700 dark:text-gray-300">
            Uploading… {Math.round(progress * 100)}%
          </p>
          <div className="mx-auto h-1.5 w-56 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
            <div
              className="h-full bg-blue-500 transition-[width] duration-150"
              style={{ width: `${Math.max(2, progress * 100)}%` }}
            />
          </div>
        </div>
      ) : (
        <>
          <Upload className="mx-auto mb-2 h-5 w-5 text-gray-400" />
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Drop files here, or{' '}
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="font-medium text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
            >
              browse
            </button>
          </p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-500">
            Images, video, audio and documents. A name and a file is all that is needed.
          </p>
        </>
      )}
    </div>
  )
}
