import client from '@/api/client'

/**
 * What the library has spent.
 *
 * Every figure here is a list-price estimate unless `estimated` says otherwise, and the
 * UI is required to say so. A cost from the pricing table is a published rate, not a
 * bill — providers change prices and offer discounts it does not model.
 */

interface DataResponse<T> {
  data: T
}

export interface UsageTotals {
  total_events: number
  /**
   * Fewer than `total_events` means some calls could not be priced — a custom endpoint,
   * or a model the table does not know — so `cost` is a floor rather than the whole
   * story.
   */
  priced_events: number
  cost: number
  currency: string
  estimated: boolean
  tokens: number
  seconds: number
}

export interface ProviderTotal {
  provider: string
  events: number
  units: number
  /** null when nothing in this group could be priced. */
  cost: number | null
}

export interface UsageSummary {
  totals: UsageTotals
  by_provider: ProviderTotal[]
}

export const usageApi = {
  summary(): Promise<UsageSummary> {
    return client.get<DataResponse<UsageSummary>>('/usage').then((r) => r.data.data)
  },

  forAsset(assetId: string): Promise<UsageTotals> {
    return client
      .get<DataResponse<UsageTotals>>(`/usage/assets/${assetId}`)
      .then((r) => r.data.data)
  },
}

/**
 * A cost as a string, at a precision that does not pretend.
 *
 * Enrichment costs land in fractions of a cent, and rounding those to two decimals shows
 * "$0.00" for work that was not free. Four decimals below a cent, two above.
 */
export function formatCost(cost: number, currency = 'USD'): string {
  const symbol = currency === 'USD' ? '$' : `${currency} `
  if (cost === 0) return `${symbol}0.00`
  if (cost < 0.01) return `${symbol}${cost.toFixed(4)}`
  return `${symbol}${cost.toFixed(2)}`
}
