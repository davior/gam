import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
import { activityApi, transcriptsApi, type ActivityJob } from '@/api/transcripts'
import { clipsApi } from '@/api/clips'
import { enrichmentApi } from '@/api/enrichment'
import { generateApi } from '@/api/generate'
import { usageApi } from '@/api/usage'
import { useActivityStore } from '@/stores/activity'
import { useGenerationStore } from '@/stores/generation'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'
import { noAttribution } from '@/test-fixtures'

const tag = (id: string, name: string): Tag => ({ id, name, category_id: null })

function makeAsset(id: string, name: string, tags: Tag[] = []): Asset {
  return {
    id,
    name,
    description: null,
    summary: null,
    ...noAttribution,
    asset_type: 'video',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
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
  vi.spyOn(usageApi, 'forAsset').mockResolvedValue({
    total_events: 0,
    priced_events: 0,
    cost: 0,
    currency: 'USD',
    estimated: true,
    tokens: 0,
    seconds: 0,
  })
  // The add panel's generate form asks for the catalogue on mount, folded or not.
  vi.spyOn(generateApi, 'models').mockResolvedValue([])
  useLibraryStore.getState().reset()
  useTagStore.getState().reset()
  useGenerationStore.getState().reset()
  // Here rather than in afterEach: the view subscribes to this store, and resetting it
  // while the last test's view is still mounted is a render outside act().
  useActivityStore.getState().reset()
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

  it('swaps to another asset without closing the panel', async () => {
    /**
     * Docked, the panel sits beside the library rather than over it, so the grid is
     * still there to click. Going through close-and-reopen for every asset would be a
     * modal's workflow surviving into something that is not one.
     */
    const user = userEvent.setup()
    render(<LibraryView />)

    await user.click(await card('First'))
    expect(await screen.findByRole('dialog')).toHaveAccessibleName('First')

    await user.click(await card('Second'))

    expect(await screen.findByRole('dialog')).toHaveAccessibleName('Second')
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

describe('LibraryView add panel', () => {
  // By role, not label: a role query skips what `hidden` hides, a label query does not.
  const linkBox = () => screen.queryByRole('textbox', { name: 'Link to import' })

  it('stays folded until + is pressed, and + folds it again', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)
    await card('First')

    const add = screen.getByRole('button', { name: 'Add to library' })
    expect(add).toHaveAttribute('aria-expanded', 'false')
    expect(linkBox()).not.toBeInTheDocument()

    await user.click(add)
    expect(add).toHaveAttribute('aria-expanded', 'true')
    expect(linkBox()).toBeVisible()
    expect(screen.getByText(/drop files here/i)).toBeVisible()

    await user.click(add)
    expect(linkBox()).not.toBeInTheDocument()
  })

  it('closes with its ✕ and puts focus back on +', async () => {
    /**
     * The ✕ is hidden by its own click, and focus left on a hidden element drops to
     * <body> — a keyboard user would be sent back to the top of the page.
     */
    const user = userEvent.setup()
    render(<LibraryView />)
    await card('First')
    const add = screen.getByRole('button', { name: 'Add to library' })

    await user.click(add)
    await user.click(screen.getByRole('button', { name: 'Close add panel' }))

    expect(linkBox()).not.toBeInTheDocument()
    expect(add).toHaveFocus()
  })

  it('keeps a half-pasted link and its options through a close', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)
    await card('First')
    const add = screen.getByRole('button', { name: 'Add to library' })

    await user.click(add)
    await user.type(
      screen.getByRole('textbox', { name: 'Link to import' }),
      'youtu.be/abc'
    )
    await user.click(screen.getByRole('checkbox', { name: 'Audio only' }))
    await user.click(screen.getByRole('button', { name: 'Close add panel' }))
    await user.click(add)

    expect(linkBox()).toHaveValue('youtu.be/abc')
    expect(screen.getByRole('checkbox', { name: 'Audio only' })).toBeChecked()
  })

  it('opens for files dragged over the library, and only for files', async () => {
    /**
     * Folded, the drop zone is not on screen to drop onto. Without this, hiding it would
     * have quietly removed drag-and-drop.
     */
    render(<LibraryView />)
    const tile = await card('First')

    fireEvent.dragEnter(tile, { dataTransfer: { types: ['text/plain'] } })
    expect(linkBox()).not.toBeInTheDocument()

    fireEvent.dragEnter(tile, { dataTransfer: { types: ['Files'] } })
    expect(linkBox()).toBeVisible()
  })

  it('shows an upload still running behind a closed panel on the +', async () => {
    render(<LibraryView />)
    await card('First')

    act(() => useLibraryStore.setState({ uploading: true, uploadProgress: 0.45 }))

    expect(screen.getByRole('button', { name: 'Add to library' })).toHaveTextContent(
      '45%'
    )
  })

  it('offers the panel from an empty library instead of pointing above it', async () => {
    const user = userEvent.setup()
    vi.spyOn(assetsApi, 'list').mockResolvedValue({
      data: [],
      total: 0,
      limit: 60,
      offset: 0,
    })
    render(<LibraryView />)

    await user.click(await screen.findByRole('button', { name: 'Add files or a link' }))

    expect(linkBox()).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Add files or a link' })
    ).not.toBeInTheDocument()
  })
})

describe('LibraryView URL imports', () => {
  function importJob(overrides: Partial<ActivityJob> = {}): ActivityJob {
    return {
      id: 'imp1',
      kind: 'enrichment',
      action: 'import_url',
      status: 'processing',
      stalled: false,
      stage: 'Downloading video',
      progress: 40,
      detail: '',
      asset_id: null,
      asset_name: 'A lecture',
      model: '',
      result_asset_id: null,
      result_asset_ids: [],
      error_message: null,
      created_at: '2026-09-28T10:00:00Z',
      updated_at: '2026-09-28T10:00:00Z',
      ...overrides,
    }
  }

  it('adds an import, and its chapter clips, when its job finishes', async () => {
    const imported = { ...makeAsset('new', 'A lecture'), source: 'url' }
    const chapter = {
      ...makeAsset('clip1', 'Opening — A lecture'),
      source: 'clip',
      parent_asset_id: 'new',
    }
    vi.spyOn(assetsApi, 'get').mockResolvedValue(imported)
    vi.spyOn(clipsApi, 'list').mockResolvedValue([chapter])
    render(<LibraryView />)
    await card('First')

    act(() => useActivityStore.setState({ jobs: [importJob()] }))
    act(() =>
      useActivityStore.setState({
        jobs: [importJob({ status: 'done', result_asset_id: 'new' })],
      })
    )

    expect(await screen.findByText('A lecture')).toBeInTheDocument()
    expect(await screen.findByText('Opening — A lecture')).toBeInTheDocument()
    expect(await screen.findByText('5 assets')).toBeInTheDocument()
  })

  it('leaves alone imports that had already finished before the library opened', async () => {
    /**
     * The activity feed on mount holds imports finished days ago. Treating "done" alone
     * as the signal would pull every one of them to the top of the grid on each visit.
     */
    const get = vi.spyOn(assetsApi, 'get')
    useActivityStore.setState({
      jobs: [importJob({ status: 'done', result_asset_id: 'old' })],
    })
    render(<LibraryView />)
    await card('First')

    expect(get).not.toHaveBeenCalled()
  })

  it('adds nothing for an import that failed', async () => {
    const get = vi.spyOn(assetsApi, 'get')
    render(<LibraryView />)
    await card('First')

    act(() => useActivityStore.setState({ jobs: [importJob()] }))
    act(() =>
      useActivityStore.setState({
        jobs: [importJob({ status: 'error', error_message: 'Private video' })],
      })
    )

    expect(get).not.toHaveBeenCalled()
  })
})

describe('LibraryView generations (M8)', () => {
  function generateJob(overrides: Partial<ActivityJob> = {}): ActivityJob {
    return {
      id: 'gen1',
      kind: 'enrichment',
      action: 'generate',
      status: 'processing',
      stalled: false,
      stage: 'Generating',
      progress: 50,
      detail: '',
      asset_id: null,
      asset_name: 'a red fox',
      model: 'fal-ai/flux/dev',
      result_asset_id: null,
      result_asset_ids: [],
      error_message: null,
      created_at: '2026-10-07T10:00:00Z',
      updated_at: '2026-10-07T10:00:00Z',
      ...overrides,
    }
  }

  const output = (id: string, name: string) => ({
    ...makeAsset(id, name),
    asset_type: 'image' as const,
    source: 'ai_generated',
  })

  it('adds every output of a finished generation, first output on top', async () => {
    const outputs: Record<string, Asset> = {
      out1: output('out1', 'Fox one'),
      out2: output('out2', 'Fox two'),
      out3: output('out3', 'Fox three'),
    }
    vi.spyOn(assetsApi, 'get').mockImplementation(async (id) => outputs[id])
    vi.spyOn(clipsApi, 'list').mockResolvedValue([])
    render(<LibraryView />)
    await card('First')

    act(() => useActivityStore.setState({ jobs: [generateJob()] }))
    act(() =>
      useActivityStore.setState({
        jobs: [
          generateJob({
            status: 'done',
            result_asset_id: 'out1',
            result_asset_ids: ['out1', 'out2', 'out3'],
          }),
        ],
      })
    )

    expect(await screen.findByText('6 assets')).toBeInTheDocument()
    expect(useLibraryStore.getState().assets.map((a) => a.id)).toEqual([
      'out1',
      'out2',
      'out3',
      'a1',
      'a2',
      'a3',
    ])
  })

  it('falls back to the single result id from a row that has no list', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(output('out1', 'Fox one'))
    vi.spyOn(clipsApi, 'list').mockResolvedValue([])
    render(<LibraryView />)
    await card('First')

    act(() => useActivityStore.setState({ jobs: [generateJob()] }))
    act(() =>
      useActivityStore.setState({
        jobs: [generateJob({ status: 'done', result_asset_id: 'out1' })],
      })
    )

    expect(await screen.findByText('Fox one')).toBeInTheDocument()
  })

  it('adds nothing for a generation that failed', async () => {
    const get = vi.spyOn(assetsApi, 'get')
    render(<LibraryView />)
    await card('First')

    act(() => useActivityStore.setState({ jobs: [generateJob()] }))
    act(() =>
      useActivityStore.setState({
        jobs: [
          generateJob({
            status: 'error',
            error_message: 'fal.ai rejected the API key — check it in Settings',
          }),
        ],
      })
    )

    expect(get).not.toHaveBeenCalled()
  })

  it('adds what a generation saved before it failed', async () => {
    // Two outputs asked for; the second never downloaded. The first is in the library
    // with its provenance, and the grid should say so rather than wait for a reload.
    vi.spyOn(assetsApi, 'get').mockResolvedValue(output('out1', 'Fox one'))
    vi.spyOn(clipsApi, 'list').mockResolvedValue([])
    render(<LibraryView />)
    await card('First')

    act(() => useActivityStore.setState({ jobs: [generateJob()] }))
    act(() =>
      useActivityStore.setState({
        jobs: [
          generateJob({
            status: 'error',
            error_message: 'Could not download the generated file (HTTP 404)',
            result_asset_id: 'out1',
            result_asset_ids: ['out1'],
          }),
        ],
      })
    )

    expect(await screen.findByText('Fox one')).toBeInTheDocument()
  })

  it('offers text → image in the add panel', async () => {
    const user = userEvent.setup()
    render(<LibraryView />)
    await card('First')

    await user.click(screen.getByRole('button', { name: 'Add to library' }))

    expect(screen.getByRole('form', { name: 'Generate an image' })).toBeVisible()
  })
})
