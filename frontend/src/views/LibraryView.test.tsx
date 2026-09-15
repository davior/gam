import { render, screen, waitFor, within } from '@testing-library/react'
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
import LibraryView from '@/views/LibraryView'
import { assetsApi, type Asset, type AssetPage } from '@/api/assets'
import { tagsApi, type Tag } from '@/api/tags'
import { activityApi, transcriptsApi } from '@/api/transcripts'
import { enrichmentApi } from '@/api/enrichment'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'

const tag = (id: string, name: string): Tag => ({ id, name, category_id: null })

function makeAsset(id: string, name: string, tags: Tag[] = []): Asset {
  return {
    id,
    name,
    description: null,
    summary: null,
    asset_type: 'video',
    source: 'local_upload',
    original_name: null,
    mime_type: null,
    file_format: null,
    size_bytes: 0,
    duration_seconds: null,
    width: null,
    height: null,
    codec: null,
    file_url: null,
    thumb_url: null,
    missing: false,
    tags,
    upload_date: '2026-01-01T00:00:00',
    modified_date: '2026-01-01T00:00:00',
    metadata_modified_date: '2026-01-01T00:00:00',
  }
}

const ASSETS = [
  makeAsset('a1', 'First'),
  makeAsset('a2', 'Second'),
  makeAsset('a3', 'Third', [tag('t1', 'interview')]),
]

let bulk: MockInstance<typeof tagsApi.bulk>

beforeEach(() => {
  vi.spyOn(assetsApi, 'list').mockResolvedValue({
    data: ASSETS,
    total: ASSETS.length,
    limit: 60,
    offset: 0,
  } satisfies AssetPage)
  vi.spyOn(tagsApi, 'list').mockResolvedValue([tag('t1', 'interview')])
  vi.spyOn(tagsApi, 'listCategories').mockResolvedValue([])
  bulk = vi.spyOn(tagsApi, 'bulk').mockResolvedValue({ updated: 0, tags_added: [] })
  // Opening the detail panel mounts the transcript panel, which fetches. Left real it
  // would reach for a dev server that is not running and fill the output with ECONNREFUSED.
  vi.spyOn(transcriptsApi, 'get').mockResolvedValue({
    asset_id: 'a1',
    status: null,
    model: null,
    language: null,
    segments: [],
  })
  vi.spyOn(activityApi, 'list').mockResolvedValue([])
  // The detail panel also mounts the suggestion panel, which loads on mount.
  vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([])
  useLibraryStore.getState().reset()
  useTagStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

/** The grid tile for an asset — the card is a button wrapping the name. */
async function card(name: string): Promise<HTMLElement> {
  const label = await screen.findByText(name)
  const button = label.closest('button')
  if (!button) throw new Error(`No card for ${name}`)
  return button
}

const selectedNames = () =>
  screen
    .getAllByRole('button', { pressed: true })
    .map((el) => el.textContent?.trim())
    .sort()

describe('LibraryView selection', () => {
  it('opens an asset on a plain click', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.click(await card('First'))
    expect(await screen.findByRole('dialog')).toHaveAccessibleName('First')
  })

  it('shows the summary, which was typed since M1 and rendered nowhere', async () => {
    const user = userEvent.setup()
    // Re-mocked rather than seeded through the store: the view loads on mount, so a
    // setState here is replaced by the list response a moment later.
    const withSummary = { ...makeAsset('a1', 'First'), summary: 'A long interview.' }
    vi.spyOn(assetsApi, 'list').mockResolvedValue({
      data: [withSummary],
      total: 1,
      limit: 60,
      offset: 0,
    })
    render(<LibraryView />)

    await user.click(await card('First'))

    expect(await screen.findByLabelText(/^summary$/i)).toHaveValue('A long interview.')
  })

  it('saves an edited summary alongside the other fields', async () => {
    const user = userEvent.setup()
    const update = vi
      .spyOn(assetsApi, 'update')
      .mockResolvedValue({ ...makeAsset('a1', 'First'), summary: 'Mine.' })
    render(<LibraryView />)

    await user.click(await card('First'))
    await user.type(await screen.findByLabelText(/^summary$/i), 'Mine.')
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(update).toHaveBeenCalled())
    expect(update.mock.calls[0][1]).toMatchObject({ summary: 'Mine.' })
  })

  it('selects instead of opening when Ctrl is held', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.keyboard('{Control>}')
    await user.click(await card('First'))
    await user.keyboard('{/Control}')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(selectedNames()).toEqual(['First'])
    expect(await screen.findByText('1 selected')).toBeInTheDocument()
  })

  it('continues a live selection on a plain click', async () => {
    /**
     * Without this, building a selection means holding a modifier for every one of
     * fifty cards — and on a touchscreen there is no modifier to hold at all.
     */
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.keyboard('{Control>}')
    await user.click(await card('First'))
    await user.keyboard('{/Control}')
    await user.click(await card('Second'))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(selectedNames()).toEqual(['First', 'Second'])
  })

  it('takes a range with Shift', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.keyboard('{Control>}')
    await user.click(await card('First'))
    await user.keyboard('{/Control}')

    await user.keyboard('{Shift>}')
    await user.click(await card('Third'))
    await user.keyboard('{/Shift}')

    expect(selectedNames()).toEqual(['First', 'Second', 'Third'])
  })

  it('clears the selection, and clicking then opens again', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.keyboard('{Control>}')
    await user.click(await card('First'))
    await user.keyboard('{/Control}')
    await user.click(screen.getByRole('button', { name: /Clear$/ }))

    expect(screen.queryByText('1 selected')).not.toBeInTheDocument()
    await user.click(await card('Second'))
    expect(await screen.findByRole('dialog')).toHaveAccessibleName('Second')
  })

  it('bulk-tags everything selected in one call', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.keyboard('{Control>}')
    await user.click(await card('First'))
    await user.click(await card('Third'))
    await user.keyboard('{/Control}')

    const toolbar = (await screen.findByText('2 selected')).parentElement as HTMLElement
    await user.type(
      within(toolbar).getByLabelText('Add a tag to all of them'),
      'archive{Enter}'
    )

    await waitFor(() => expect(bulk).toHaveBeenCalledTimes(1))
    expect(bulk.mock.calls[0][0].sort()).toEqual(['a1', 'a3'])
    expect(bulk.mock.calls[0][1]).toEqual(['archive'])
  })

  it('offers removal only of tags actually on the selection', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)

    // "First" carries no tags, so there is nothing to remove.
    await user.keyboard('{Control>}')
    await user.click(await card('First'))
    await user.keyboard('{/Control}')
    expect(screen.queryByLabelText('Remove a tag')).not.toBeInTheDocument()

    await user.click(await card('Third'))
    const remove = await screen.findByLabelText('Remove a tag')
    expect(within(remove).getByRole('option', { name: /interview/ })).toBeInTheDocument()

    await user.selectOptions(remove, 't1')
    await waitFor(() => expect(bulk).toHaveBeenCalledTimes(1))
    expect(bulk.mock.calls[0][2]).toEqual(['t1'])
  })

  it('selects every loaded asset at once', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.keyboard('{Control>}')
    await user.click(await card('First'))
    await user.keyboard('{/Control}')
    await user.click(screen.getByRole('button', { name: 'Select all loaded' }))

    expect(selectedNames()).toEqual(['First', 'Second', 'Third'])
  })

  it('drops selected ids that a filter change took off screen', async () => {
    /**
     * Otherwise a bulk tag would land on rows the user can no longer see — they picked
     * three, changed a filter, and tagged something that is not in front of them.
     */
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.keyboard('{Control>}')
    await user.click(await card('First'))
    await user.click(await card('Second'))
    await user.keyboard('{/Control}')
    expect(await screen.findByText('2 selected')).toBeInTheDocument()

    vi.spyOn(assetsApi, 'list').mockResolvedValue({
      data: [ASSETS[0]],
      total: 1,
      limit: 60,
      offset: 0,
    })
    await user.click(screen.getByRole('button', { name: 'Images' }))

    expect(await screen.findByText('1 selected')).toBeInTheDocument()
  })
})
