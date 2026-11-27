import client from '@/api/client'
import type { Asset } from '@/api/assets'

/**
 * Pictures of a PDF's pages, over `routers/documents.py`.
 *
 * A PDF cannot go in a playlist or over a video; a rendered page is an ordinary image
 * that can, which is the reason this exists.
 */

interface DataResponse<T> {
  data: T
}

export interface GrabPageOptions {
  /** Counted from 1. */
  page: number
  /** Render scale; the server's default (2) is sharp at 1080p. */
  scale?: number
  name?: string
}

export const documentsApi = {
  /** How many pages the PDF has. Read from the file: no column holds it. */
  pageCount(assetId: string): Promise<number> {
    return client
      .get<DataResponse<{ page_count: number }>>(`/assets/${assetId}/pages`)
      .then((r) => r.data.data.page_count)
  },

  /** Renders one page and files it in the library as an image, resolving at once: a
   *  page renders in well under a second, so there is no job to wait on. */
  grabPage(assetId: string, options: GrabPageOptions): Promise<Asset> {
    return client
      .post<DataResponse<Asset>>(`/assets/${assetId}/grab-page`, options)
      .then((r) => r.data.data)
  },
}
