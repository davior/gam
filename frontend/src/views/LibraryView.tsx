import { Library } from 'lucide-react'

/**
 * The asset library.
 *
 * A placeholder until M1, which brings ingestion and the grid. It exists now so the
 * shell, routing and auth gate have something real to render and can be tested.
 */
export default function LibraryView() {
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center gap-3 px-4 py-16 text-center">
      <Library className="h-10 w-10 text-gray-400 dark:text-gray-500" />
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        Your library is empty
      </h2>
      <p className="text-sm text-gray-600 dark:text-gray-400">
        Uploading, thumbnails and search arrive in the next milestone. Until then this
        page proves the session, the shell and the API are talking to each other.
      </p>
    </div>
  )
}
