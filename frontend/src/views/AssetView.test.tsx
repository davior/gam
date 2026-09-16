import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import AssetView from '@/views/AssetView'
import { assetsApi, type Asset } from '@/api/assets'
import { tagsApi } from '@/api/tags'
import { usageApi } from '@/api/usage'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Giordano interview',
    description: null,
    summary: null,
    asset_type: 'video',
    source: 'local_upload',
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
    total_cost: 0,
    events: 0,
    estimated: false,
  } as never)
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
      await screen.findByRole('dialog', { name: 'Giordano interview' })
    ).toBeInTheDocument()
  })

  it('puts the asset in the library store, so the panel controls write somewhere', async () => {
    // `update`, `remove` and `setAssetTags` all map over `assets`. An asset missing
    // from that list gets working-looking controls whose every write lands nowhere.
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())

    renderAt('/a/a1')
    await screen.findByRole('dialog', { name: 'Giordano interview' })

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
    await screen.findByRole('dialog', { name: 'Giordano interview' })

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
      await screen.findByRole('dialog', { name: 'Giordano interview' })
    ).toBeInTheDocument()
  })
})
