import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from 'vitest'
import FilterBar from '@/components/FilterBar'
import { assetsApi, type AssetPage, type ListAssetsParams } from '@/api/assets'
import { tagsApi, type Tag } from '@/api/tags'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'

const tag = (id: string, name: string): Tag => ({ id, name, category_id: null })

let list: MockInstance<(params?: ListAssetsParams) => Promise<AssetPage>>

function listCalls() {
  return list.mock.calls
}

/**
 * The params of the most recent listing request — what the filters actually did.
 *
 * `{}` when nothing was requested at all: FilterBar does not load on mount (LibraryView
 * does), so "no call" is a meaningful state and must not read as a crash.
 */
function lastParams(): ListAssetsParams {
  const calls = listCalls()
  return calls.length > 0 ? (calls[calls.length - 1][0] ?? {}) : {}
}

beforeEach(() => {
  list = vi
    .spyOn(assetsApi, 'list')
    .mockResolvedValue({ data: [], total: 0, limit: 60, offset: 0 })
  vi.spyOn(tagsApi, 'list').mockResolvedValue([tag('t1', 'NATO'), tag('t2', 'archive')])
  vi.spyOn(tagsApi, 'listCategories').mockResolvedValue([
    { id: 'c1', name: 'People', parent_category_id: null },
    { id: 'c2', name: 'Scientists', parent_category_id: 'c1' },
  ])
  useLibraryStore.getState().reset()
  useTagStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('FilterBar', () => {
  it('sends a type filter', async () => {
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(screen.getByRole('button', { name: 'Video' }))
    await waitFor(() => expect(lastParams().asset_type).toBe('video'))
  })

  it('sends a tag filter, and ANDs a second one', async () => {
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(await screen.findByRole('button', { name: /NATO/ }))
    await waitFor(() => expect(lastParams().tag).toEqual(['NATO']))

    await user.click(screen.getByRole('button', { name: /archive/ }))
    await waitFor(() => expect(lastParams().tag).toEqual(['NATO', 'archive']))
  })

  it('clears one filter from its chip without touching the others', async () => {
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(screen.getByRole('button', { name: 'Video' }))
    await user.click(await screen.findByRole('button', { name: /NATO/ }))
    await waitFor(() => expect(lastParams().tag).toEqual(['NATO']))

    await user.click(screen.getByRole('button', { name: 'Clear NATO' }))
    await waitFor(() => expect(lastParams().tag).toBeUndefined())
    // The type filter is untouched: one ✕ clears one thing.
    expect(lastParams().asset_type).toBe('video')
  })

  it('clears everything at once', async () => {
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(screen.getByRole('button', { name: 'Video' }))
    await user.click(await screen.findByRole('button', { name: /NATO/ }))
    await waitFor(() => expect(lastParams().tag).toEqual(['NATO']))

    await user.click(screen.getByRole('button', { name: 'Clear all' }))
    await waitFor(() => {
      expect(lastParams().tag).toBeUndefined()
      expect(lastParams().asset_type).toBeUndefined()
    })
  })

  it('shows an active filter as a chip even while its panel is folded away', async () => {
    /**
     * The whole reason the disclosure is safe. A filter the user cannot see is a filter
     * they will blame the library for.
     */
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(screen.getByRole('button', { name: /More filters/ }))
    await user.selectOptions(screen.getByLabelText('Source'), 'ai_generated')
    await waitFor(() => expect(lastParams().source).toBe('ai_generated'))

    await user.click(screen.getByRole('button', { name: /More filters/ }))
    expect(screen.queryByLabelText('Source')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear AI generated' })).toBeInTheDocument()
  })

  it('refuses an inverted duration range instead of letting the server 400', async () => {
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(screen.getByRole('button', { name: /More filters/ }))
    await user.type(screen.getByLabelText('Minimum duration in seconds'), '100')
    await user.type(screen.getByLabelText('Maximum duration in seconds'), '10')
    await user.tab()

    expect(await screen.findByRole('alert')).toHaveTextContent(/minimum is longer/i)
    expect(lastParams().min_duration).toBeUndefined()
    expect(lastParams().max_duration).toBeUndefined()

    // And it is refused for being inverted, not because the control is inert: fix the
    // maximum and the same range commits. Without this the test would still pass if
    // duration filtering never worked at all.
    await user.clear(screen.getByLabelText('Maximum duration in seconds'))
    await user.type(screen.getByLabelText('Maximum duration in seconds'), '1000')
    await user.tab()

    await waitFor(() => expect(lastParams().min_duration).toBe(100))
    expect(lastParams().max_duration).toBe(1000)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('sends a valid duration range', async () => {
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(screen.getByRole('button', { name: /More filters/ }))
    await user.type(screen.getByLabelText('Minimum duration in seconds'), '60{Enter}')

    await waitFor(() => expect(lastParams().min_duration).toBe(60))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not commit half a range while moving between the two fields', async () => {
    /**
     * Tabbing from the minimum to the maximum used to commit `min` on its own. Then an
     * inverted pair was refused — and the refused message sat over a grid that really
     * was filtered by that stray minimum. Moving between the halves of one control is
     * not finishing with it.
     */
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(screen.getByRole('button', { name: /More filters/ }))
    const before = listCalls().length
    await user.type(screen.getByLabelText('Minimum duration in seconds'), '100')
    await user.tab()

    expect(document.activeElement).toBe(
      screen.getByLabelText('Maximum duration in seconds')
    )
    expect(listCalls()).toHaveLength(before)
  })

  it('offers nested categories under their parent', async () => {
    const user = userEvent.setup()
    render(<FilterBar />)

    await user.click(screen.getByRole('button', { name: /More filters/ }))
    const select = await screen.findByLabelText('Category')
    // Indented rather than flat, so the tree is legible in a control that has no tree.
    expect(
      Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    ).toEqual(['Any category', 'People', '  Scientists'])

    await user.selectOptions(select, 'c2')
    await waitFor(() => expect(lastParams().category_id).toBe('c2'))
  })
})
