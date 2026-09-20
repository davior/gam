import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import AssetView from '@/views/AssetView'
import { assetsApi, type Asset } from '@/api/assets'
import { clipsApi } from '@/api/clips'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { tagsApi } from '@/api/tags'
import { usageApi } from '@/api/usage'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'
import { noAttribution } from '@/test-fixtures'

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Giordano interview',
    description: null,
    summary: null,
    ...noAttribution,
    asset_type: 'video',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: 'giordano.mp4',
    mime_type: 'video/mp4',
    file_format: 'mp4',
    size_bytes: 100,
    duration_seconds: 3600,
    width: 1920,
    height: 1080,
    codec: 'h264',
    file_url: '/media/u/a1.mp4?exp=1&sig=x',
    thumb_url: '/media/u/a1.thumb.jpg?exp=1&sig=x',
    missing: false,
    tags: [],
    upload_date: '2026-09-13T10:00:00Z',
    modified_date: '2026-09-13T10:00:00Z',
    metadata_modified_date: '2026-09-13T10:00:00Z',
    ...overrides,
  }
}

function notFound(): AxiosError {
  const error = new AxiosError('Not Found')
  error.response = {
    status: 404,
    statusText: 'Not Found',
    data: { detail: { code: 'not_found', message: 'No such asset' } },
    headers: {},
    config: { headers: new AxiosHeaders() },
  }
  return error
}

function activityJob(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    kind: 'enrichment',
    action: 'extract_subvideo',
    status: 'done',
    stalled: false,
    stage: 'Done',
    progress: 100,
    detail: '',
    asset_id: 'clip1',
    asset_name: 'Clip of Giordano interview',
    model: '',
    result_asset_id: null,
    error_message: null,
    created_at: '2026-09-18T00:00:00Z',
    updated_at: '2026-09-18T00:00:00Z',
    ...overrides,
  }
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/a/:id" element={<AssetView />} />
        <Route path="/library" element={<p>The library</p>} />
      </Routes>
    </MemoryRouter>
  )
}

beforeEach(() => {
  useLibraryStore.getState().reset()
  useTagStore.getState().reset()
  // AssetDetail calls `ensureLoaded` — it is reachable from search, where no FilterBar
  // has loaded the catalogue. Stubbed so the suite makes no real request.
  vi.spyOn(tagsApi, 'list').mockResolvedValue([])
  vi.spyOn(tagsApi, 'listCategories').mockResolvedValue([])
  vi.spyOn(usageApi, 'forAsset').mockResolvedValue({
    total_events: 0,
    priced_events: 0,
    cost: 0,
    currency: 'USD',
    estimated: true,
    tokens: 0,
    seconds: 0,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AssetView', () => {
  it('loads an asset that was never in the library list', async () => {
    // The point of the route: a link from Notes lands here with an empty store.
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())

    renderAt('/a/a1')

    expect(
      await screen.findByRole('heading', { name: 'Giordano interview' })
    ).toBeInTheDocument()
  })

  it('puts the asset in the library store, so the panel controls write somewhere', async () => {
    // `update`, `remove` and `setAssetTags` all map over `assets`. An asset missing
    // from that list gets working-looking controls whose every write lands nowhere.
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())

    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    expect(useLibraryStore.getState().assets.map((a) => a.id)).toEqual(['a1'])
  })

  it('says so when the asset is gone, rather than redirecting silently', async () => {
    vi.spyOn(assetsApi, 'get').mockRejectedValue(notFound())

    renderAt('/a/missing')

    expect(await screen.findByText(/that asset is not here/i)).toBeInTheDocument()
  })

  it('reads a timestamp off the URL', async () => {
    const spy = vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())

    renderAt('/a/a1?t=412.5')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    expect(spy).toHaveBeenCalledWith('a1')
    // The player seeks on `loadedmetadata`, which jsdom never fires, so the assertion
    // that matters here is that a malformed value cannot reach it — see below.
  })

  it('ignores a timestamp that is not a number', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())

    renderAt('/a/a1?t=banana')

    // Nothing to assert on the player, but rendering at all proves NaN did not reach
    // `currentTime`, which throws in a real browser.
    expect(
      await screen.findByRole('heading', { name: 'Giordano interview' })
    ).toBeInTheDocument()
  })
})

describe('clips (M7)', () => {
  function clip(overrides: Partial<Asset> = {}): Asset {
    return asset({
      id: 'clip1',
      name: 'Clip of Giordano interview (1:01–1:10)',
      source: 'clip',
      parent_asset_id: 'a1',
      in_point: 61,
      out_point: 70,
      duration_seconds: 9,
      // A clip's URLs resolve to its parent's keys server-side (to_read_model) — the
      // fixture reflects what the API actually returns, not a null file_url.
      file_url: '/media/u/a1.mp4?exp=1&sig=x',
      thumb_url: '/media/u/a1.thumb.jpg?exp=1&sig=x',
      ...overrides,
    })
  }

  it('offers a Clip tab and a Transcript tab for a real video', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    expect(screen.getByRole('tab', { name: 'Clip' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Transcript' })).toBeInTheDocument()
  })

  it('hides the Clip and Transcript tabs, and every AI enrichment control, for a clip', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(clip())
    renderAt('/a/clip1')
    await screen.findByRole('heading', { name: /clip of giordano/i })

    expect(screen.queryByRole('tab', { name: 'Clip' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Transcript' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Generate all' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Describe with AI' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Summarise with AI' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Suggest tags and a title' })
    ).not.toBeInTheDocument()
  })

  it('shows the same AI enrichment controls as ever for an ordinary asset', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    expect(screen.getByRole('button', { name: 'Generate all' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Describe with AI' })).toBeInTheDocument()
  })

  it('shows the range in the Info tab for a clip', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(clip())
    renderAt('/a/clip1')
    await screen.findByRole('heading', { name: /clip of giordano/i })

    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    expect(screen.getByText('1:01–1:10')).toBeInTheDocument()
  })

  it('blocks deleting an asset with dependent clips and offers to promote them', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    // The guard is a proactive `clipsApi.list` check before `remove()` is ever
    // called — not a reaction to a 409 from it — so `assetsApi.remove` is not mocked
    // to reject here; a real bug in the guard would show up as it being called at all.
    const removeAsset = vi.spyOn(assetsApi, 'remove').mockResolvedValue(undefined)
    vi.spyOn(clipsApi, 'list').mockResolvedValue([
      clip({ id: 'clip1', name: 'Clip one' }),
      clip({ id: 'clip2', name: 'Clip two' }),
    ])

    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))

    const notice = await screen.findByText(/2 clips depend on this asset/i)
    // Scoped to the guard's own box: the Clip tab's own (currently hidden, but still
    // mounted — Tabs.tsx keeps every panel mounted) list renders the same two names.
    const guard = within(notice.closest('div')!)
    expect(guard.getByText('Clip one')).toBeInTheDocument()
    expect(guard.getByText('Clip two')).toBeInTheDocument()
    expect(removeAsset).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Promote and delete' })).toBeInTheDocument()
  })

  it('promoting every dependent clip retries the delete, which then succeeds', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    // The guard is a proactive check (`clipsApi.list`), not a reaction to `remove()`
    // failing — `assetsApi.remove` is never even called until the clips are clear, so
    // this only ever needs to resolve, and only once.
    const removeAsset = vi.spyOn(assetsApi, 'remove').mockResolvedValue(undefined)
    vi.spyOn(clipsApi, 'list').mockResolvedValue([clip()])
    const promote = vi
      .spyOn(clipsApi, 'promote')
      .mockResolvedValue(activityJob({ status: 'processing' }))
    vi.spyOn(activityApi, 'get').mockResolvedValue(activityJob({ status: 'done' }))

    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await screen.findByRole('button', { name: 'Promote and delete' })

    await userEvent.click(screen.getByRole('button', { name: 'Promote and delete' }))

    expect(promote).toHaveBeenCalledWith('clip1')
    // Only navigates home once the delete actually went through, after promoting.
    await waitFor(() => expect(removeAsset).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('The library')).toBeInTheDocument()
  })

  it('surfaces a failed promote instead of silently retrying the delete', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    const removeAsset = vi.spyOn(assetsApi, 'remove').mockResolvedValue(undefined)
    vi.spyOn(clipsApi, 'list').mockResolvedValue([clip()])
    vi.spyOn(clipsApi, 'promote').mockResolvedValue(activityJob({ status: 'processing' }))
    vi.spyOn(activityApi, 'get').mockResolvedValue(
      activityJob({
        status: 'error',
        error_message: 'ffmpeg is not available on this server',
      })
    )

    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'Promote and delete' })
    )

    expect(await screen.findByText(/ffmpeg is not available/i)).toBeInTheDocument()
    // Never even attempted — the guard's whole job is to keep a doomed delete from
    // being tried in the first place, not to clean up after one.
    expect(removeAsset).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Promote and delete' })).toBeInTheDocument()
  })

  it('pauses playback once it reaches a clip’s out point', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(clip())
    const pause = vi
      .spyOn(HTMLMediaElement.prototype, 'pause')
      .mockImplementation(() => {})

    renderAt('/a/clip1')
    await screen.findByRole('heading', { name: /clip of giordano/i })

    const video = document.querySelector('video')
    expect(video).not.toBeNull()
    Object.defineProperty(video!, 'currentTime', { value: 70, writable: true })
    video!.dispatchEvent(new Event('timeupdate'))

    expect(pause).toHaveBeenCalled()
  })
})
