import type { Asset } from '@/api/assets'

/**
 * The attribution half of an `Asset`, empty.
 *
 * Six test files build their own asset literals, and M10 added ten fields to the shape.
 * Spreading this into each keeps them compiling without duplicating the list six times —
 * and means the next field added to `Asset` is one edit here rather than six.
 *
 * Deliberately only the attribution block rather than a whole `makeAsset` factory: each
 * of those files varies a different part of the asset, and replacing their factories
 * with one shared default would flatten distinctions their tests depend on.
 */
export const noAttribution: Pick<
  Asset,
  | 'source_url'
  | 'creator'
  | 'publisher'
  | 'source_title'
  | 'published_date'
  | 'retrieved_at'
  | 'license'
  | 'credit_line'
  | 'credit'
  | 'attribution_inherited'
> = {
  source_url: null,
  creator: null,
  publisher: null,
  source_title: null,
  published_date: null,
  retrieved_at: null,
  license: null,
  credit_line: null,
  credit: '',
  attribution_inherited: [],
}
