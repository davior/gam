import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import UsagePanel from '@/components/UsagePanel'
import { formatCost, usageApi, type UsageSummary } from '@/api/usage'

function summary(
  overrides: Partial<UsageSummary['totals']> = {},
  by_provider = []
): UsageSummary {
  return {
    totals: {
      total_events: 4,
      priced_events: 4,
      cost: 0.1834,
      currency: 'USD',
      estimated: true,
      tokens: 120_000,
      seconds: 0,
      ...overrides,
    },
    by_provider,
  }
}

beforeEach(() => {
  vi.spyOn(usageApi, 'summary').mockResolvedValue(summary())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('UsagePanel', () => {
  it('never shows a figure without saying it is an estimate', async () => {
    // The rule the milestone doc sets: a cost from the pricing table is a published list
    // price, not a bill, and GAM must not show one without saying so.
    render(<UsagePanel />)

    expect(await screen.findByText('$0.18')).toBeInTheDocument()
    expect(screen.getByText(/not a bill/i)).toBeInTheDocument()
  })

  it('says so when some calls could not be priced', async () => {
    // A total that silently omitted them would read as complete.
    vi.spyOn(usageApi, 'summary').mockResolvedValue(
      summary({ total_events: 5, priced_events: 3 })
    )
    render(<UsagePanel />)

    expect(
      await screen.findByText(/2 of 5 calls could not be priced/i)
    ).toBeInTheDocument()
    expect(screen.getByText(/excludes them/i)).toBeInTheDocument()
  })

  it('does not raise that caveat when everything was priced', async () => {
    render(<UsagePanel />)

    await screen.findByText('$0.18')
    expect(screen.queryByText(/could not be priced/i)).toBeNull()
  })

  it('breaks the total down by provider', async () => {
    vi.spyOn(usageApi, 'summary').mockResolvedValue(
      summary({}, [
        { provider: 'anthropic', events: 3, units: 100_000, cost: 0.18 },
        { provider: 'custom', events: 1, units: 20_000, cost: null },
      ] as never)
    )
    render(<UsagePanel />)

    expect(await screen.findByText('Anthropic')).toBeInTheDocument()
    // An unpriced group says what it is rather than showing a misleading zero.
    expect(screen.getByText(/1 call, not priced/i)).toBeInTheDocument()
  })

  it('reports transcription in minutes, since it is not costed', async () => {
    vi.spyOn(usageApi, 'summary').mockResolvedValue(summary({ seconds: 7200 }))
    render(<UsagePanel />)

    expect(await screen.findByText('120 minutes')).toBeInTheDocument()
  })

  it('says nothing has been spent rather than showing a zero', async () => {
    vi.spyOn(usageApi, 'summary').mockResolvedValue(
      summary({ total_events: 0, priced_events: 0, cost: 0, tokens: 0 })
    )
    render(<UsagePanel />)

    expect(await screen.findByText(/nothing yet/i)).toBeInTheDocument()
    expect(screen.queryByText(/\$/)).toBeNull()
  })

  it('surfaces a failure to load', async () => {
    vi.spyOn(usageApi, 'summary').mockRejectedValue({})
    render(<UsagePanel />)

    expect(await screen.findByText(/could not load usage/i)).toBeInTheDocument()
  })
})

describe('formatCost', () => {
  it('does not round a fraction of a cent down to nothing', () => {
    // Enrichment lands here constantly. "$0.00" for work that was not free is a lie.
    expect(formatCost(0.0004)).toBe('$0.0004')
  })

  it('uses two decimals once there is a cent to show', () => {
    expect(formatCost(1.239)).toBe('$1.24')
  })

  it('shows a real zero plainly', () => {
    expect(formatCost(0)).toBe('$0.00')
  })

  it('handles a currency it has no symbol for', () => {
    expect(formatCost(2.5, 'EUR')).toBe('EUR 2.50')
  })
})
