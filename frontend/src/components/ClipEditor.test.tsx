import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import ClipEditor from '@/components/ClipEditor'
import { clipsApi } from '@/api/clips'
import { activityApi } from '@/api/transcripts'
import { assetsApi, type Asset } from '@/api/assets'
import { useLibraryStore } from '@/stores/library'
import { noAttribution } from '@/test-fixtures'

function makeAsset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Interview',
    description: null,
    summary: null,
    asset_type: 'video',
    source: 'upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: null,
    mime_type: null,
    file_format: null,
    size_bytes: 0,
    duration_seconds: 60,
    width: null,
    height: null,
    codec: null,
    file_url: null,
    thumb_url: null,
    missing: false,
    tags: [],
    ...noAttribution,
    upload_date: '2026-01-01T00:00:00',
    modified_date: '2026-01-01T00:00:00',
    metadata_modified_date: '2026-01-01T00:00:00',
    ...overrides,
  }
}

const liveClip = makeAsset({
  id: 'c1',
  name: 'Live clip',
  source: 'clip',
  parent_asset_id: 'a1',
  in_point: 5,
  out_point: 9,
})
const subVideo = makeAsset({
  id: 'c2',
  name: 'Standalone cut',
  source: 'sub_video',
  parent_asset_id: 'a1',
  in_point: 10,
  out_point: 20,
})

function extractionInProgress(): AxiosError {
  const error = new AxiosError('Conflict')
  error.response = {
    status: 409,
    statusText: 'Conflict',
    data: {
      detail: {
        code: 'extraction_in_progress',
        message: 'An extraction is still running on this asset or one of its clips.',
      },
    },
    headers: {},
    config: { headers: new AxiosHeaders() },
  }
  return error
}

beforeEach(() => {
  useLibraryStore.getState().reset()
  vi.spyOn(activityApi, 'list').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

function renderEditor() {
  render(
    <MemoryRouter>
      <ClipEditor asset={makeAsset()} currentTime={0} onSeek={vi.fn()} />
    </MemoryRouter>
  )
}

describe('ClipEditor', () => {
  it('offers "Extract as file" on a live clip but not on an extracted one', async () => {
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip, subVideo])

    renderEditor()

    // Exactly one: the sub-video already owns a file.
    expect(
      await screen.findAllByRole('button', { name: 'Extract as file' })
    ).toHaveLength(1)
  })

  it("promotes the clip in place using the clip's own id", async () => {
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip])
    const promote = vi.spyOn(clipsApi, 'promote').mockResolvedValue({
      id: 'j1',
      asset_id: 'c1',
      action: 'extract_subvideo',
      status: 'queued',
    } as never)

    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: 'Extract as file' }))

    expect(promote).toHaveBeenCalledWith('c1')
  })

  it('links each clip to its own page', async () => {
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip, subVideo])

    renderEditor()

    // A live clip too, not only an extracted one: its own page is where its tags,
    // attribution and Info-tab Delete are.
    expect(await screen.findByRole('link', { name: 'Live clip' })).toHaveAttribute(
      'href',
      '/a/c1'
    )
    expect(screen.getByRole('link', { name: 'Standalone cut' })).toHaveAttribute(
      'href',
      '/a/c2'
    )
  })

  it('puts a saved clip in the library grid', async () => {
    // Before, it only reached the grid on the next reload — and with it, the only
    // delete a clip had.
    vi.spyOn(clipsApi, 'list').mockResolvedValue([])
    const saved = makeAsset({
      id: 'c3',
      name: 'New clip',
      source: 'clip',
      parent_asset_id: 'a1',
      in_point: 5,
      out_point: 9,
    })
    const create = vi.spyOn(clipsApi, 'create').mockResolvedValue(saved)

    const { rerender } = render(
      <MemoryRouter>
        <ClipEditor asset={makeAsset()} currentTime={5} onSeek={vi.fn()} />
      </MemoryRouter>
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Set in point' }))
    rerender(
      <MemoryRouter>
        <ClipEditor asset={makeAsset()} currentTime={9} onSeek={vi.fn()} />
      </MemoryRouter>
    )
    await userEvent.click(screen.getByRole('button', { name: 'Set out point' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save as clip' }))

    expect(create).toHaveBeenCalledWith('a1', { in_point: 5, out_point: 9 })
    await waitFor(() => expect(useLibraryStore.getState().assets[0]?.id).toBe('c3'))
    expect(useLibraryStore.getState().total).toBe(1)
  })
})

describe('ClipEditor, deleting a clip', () => {
  it('asks before deleting a live clip, then removes it from the list and the grid', async () => {
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip, subVideo])
    const remove = vi.spyOn(assetsApi, 'remove').mockResolvedValue(undefined)
    useLibraryStore.setState({ assets: [liveClip], total: 1 })

    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete this clip' }))

    // The trash icon only asks.
    expect(remove).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Delete clip' }))

    expect(remove).toHaveBeenCalledWith('c1')
    await waitFor(() => expect(screen.queryByText('Live clip')).not.toBeInTheDocument())
    expect(screen.getByText('Standalone cut')).toBeInTheDocument()
    expect(useLibraryStore.getState().assets).toEqual([])
    expect(useLibraryStore.getState().total).toBe(0)
  })

  it('offers no delete on an extracted sub-video', async () => {
    // It owns real bytes; removing those goes through its own page's confirm, not a
    // trash icon in a list of windows into the parent.
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip, subVideo])

    renderEditor()

    expect(
      await screen.findAllByRole('button', { name: 'Delete this clip' })
    ).toHaveLength(1)
  })

  it('keeps the clip when the confirm is cancelled', async () => {
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip])
    const remove = vi.spyOn(assetsApi, 'remove').mockResolvedValue(undefined)

    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete this clip' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(remove).not.toHaveBeenCalled()
    expect(screen.getByText('Live clip')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete this clip' })).toBeInTheDocument()
  })

  it('shows why a delete failed and keeps the row', async () => {
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip])
    vi.spyOn(assetsApi, 'remove').mockRejectedValue(extractionInProgress())

    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete this clip' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete clip' }))

    expect(await screen.findByText(/an extraction is still running/i)).toBeInTheDocument()
    expect(screen.getByText('Live clip')).toBeInTheDocument()
  })
})
