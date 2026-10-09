import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import AssetView from '@/views/AssetView'
import { assetsApi, type Asset } from '@/api/assets'
import { clipsApi } from '@/api/clips'
import { enrichmentApi } from '@/api/enrichment'
import { generateApi, type AssetGeneration } from '@/api/generate'
import { activityApi, transcriptsApi, type ActivityJob } from '@/api/transcripts'
import { tagsApi } from '@/api/tags'
import { usageApi } from '@/api/usage'
import { useActivityStore } from '@/stores/activity'
import { useGenerationStore } from '@/stores/generation'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'
import { makeGenerationModel, noAttribution } from '@/test-fixtures'

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

function refused(status: number, code: string, message: string): AxiosError {
  const error = new AxiosError(message)
  error.response = {
    status,
    statusText: '',
    data: { detail: { code, message } },
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
    result_asset_ids: [],
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
    expect(screen.getByRole('button', { name: 'Delete clips too' })).toBeInTheDocument()
  })

  it('says "depends" for a single clip', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    vi.spyOn(clipsApi, 'list').mockResolvedValue([clip()])

    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText(/^1 clip depends on this asset/i)).toBeInTheDocument()
  })

  it('offers to delete the clips along with the asset', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    const removeAsset = vi.spyOn(assetsApi, 'remove').mockResolvedValue(undefined)
    const promote = vi.spyOn(clipsApi, 'promote')
    vi.spyOn(clipsApi, 'list').mockResolvedValue([
      clip({ id: 'clip1', name: 'Clip one' }),
      clip({ id: 'clip2', name: 'Clip two' }),
    ])

    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Delete clips too' }))

    // One request: the server deletes the clips and the asset in one transaction.
    expect(removeAsset).toHaveBeenCalledTimes(1)
    expect(removeAsset).toHaveBeenCalledWith('a1', { withClips: true })
    expect(promote).not.toHaveBeenCalled()
    expect(await screen.findByText('The library')).toBeInTheDocument()
  })

  it('keeps the guard open and says why when deleting with clips fails', async () => {
    // The store waits for the server before dropping anything, so a failure leaves this
    // very panel mounted to say so. An optimistic drop would unmount it and mount a
    // fresh one with no guard and no message — the remount bug M7 already hit once.
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    vi.spyOn(assetsApi, 'remove').mockRejectedValue(new Error('The server fell over'))
    vi.spyOn(clipsApi, 'list').mockResolvedValue([clip()])

    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Delete clips too' }))

    expect(await screen.findByText('The server fell over')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete clips too' })).toBeEnabled()
    expect(
      screen.getByRole('heading', { name: 'Giordano interview' })
    ).toBeInTheDocument()
    expect(screen.queryByText('The library')).not.toBeInTheDocument()
    expect(useLibraryStore.getState().assets.map((a) => a.id)).toEqual(['a1'])
  })

  it('talks about a clip, not a file, when deleting a clip', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(clip())
    const removeAsset = vi.spyOn(assetsApi, 'remove').mockResolvedValue(undefined)
    const listClips = vi.spyOn(clipsApi, 'list')

    renderAt('/a/clip1')
    await screen.findByRole('heading', { name: /clip of giordano/i })

    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))

    expect(screen.getByText(/delete this clip\?/i)).toBeInTheDocument()
    expect(screen.getByText(/video it was cut from is not affected/i)).toBeInTheDocument()
    expect(screen.queryByText(/and its file/i)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))

    expect(removeAsset).toHaveBeenCalledWith('clip1', { withClips: false })
    // A clip cannot have clips of its own, so there is nothing for the guard to ask.
    expect(listClips).not.toHaveBeenCalled()
    expect(await screen.findByText('The library')).toBeInTheDocument()
  })

  it("says why, on the clip's own page, when the server refuses to delete it", async () => {
    // A clip being extracted as a file cannot be deleted mid-way. The reason used to
    // vanish: the optimistic delete unmounted this page and the refusal remounted it.
    vi.spyOn(assetsApi, 'get').mockResolvedValue(clip())
    vi.spyOn(assetsApi, 'remove').mockRejectedValue(
      refused(
        409,
        'extraction_in_progress',
        'An extraction is still running on this asset.'
      )
    )

    renderAt('/a/clip1')
    await screen.findByRole('heading', { name: /clip of giordano/i })
    await userEvent.click(screen.getByRole('tab', { name: 'Info' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))

    expect(
      await screen.findByText('An extraction is still running on this asset.')
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /clip of giordano/i })).toBeInTheDocument()
    expect(useLibraryStore.getState().error).toBeNull()
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

describe('generation (M8)', () => {
  function made(overrides: Partial<AssetGeneration> = {}): AssetGeneration {
    return {
      model: 'fal-ai/flux-pro/kontext',
      kind: 'image_to_image',
      prompt: 'the same fox, at dusk',
      parameters: { aspect_ratio: '16:9', num_images: 1, enable_safety_checker: true },
      sources: [{ asset_id: 'base1', role: 'base', name: 'Fox photo' }],
      seed: 1234,
      generated_at: '2026-10-07T10:00:00',
      ...overrides,
    }
  }

  function generated(overrides: Partial<AssetGeneration> = {}): Asset {
    return asset({
      id: 'g1',
      name: 'the same fox, at dusk',
      asset_type: 'image',
      source: 'ai_generated',
      original_name: 'output.png',
      mime_type: 'image/png',
      file_format: 'png',
      duration_seconds: null,
      codec: null,
      file_url: '/media/u/g1.png?exp=1&sig=x',
      thumb_url: '/media/u/g1.thumb.jpg?exp=1&sig=x',
      generation: made(overrides),
    })
  }

  const base = () =>
    asset({
      id: 'base1',
      name: 'Fox photo',
      asset_type: 'image',
      file_url: '/media/u/base1.jpg',
      thumb_url: '/media/u/base1.thumb.jpg',
    })

  const KONTEXT = makeGenerationModel({
    id: 'kontext',
    endpoint_id: 'fal-ai/flux-pro/kontext',
    kind: 'image_to_image',
    label: 'FLUX.1 Kontext [pro]',
    image_field: 'image_url',
    max_images: 1,
    options: { aspect_ratios: ['1:1', '16:9'], supports_seed: true },
  })

  function queued(): ActivityJob {
    return activityJob({
      id: 'regen1',
      action: 'generate',
      status: 'queued',
      asset_id: null,
      asset_name: 'the same fox, at dusk',
    })
  }

  async function openGenerateTab(path = '/a/g1') {
    renderAt(path)
    await userEvent.click(await screen.findByRole('tab', { name: 'Generate' }))
  }

  beforeEach(() => {
    useActivityStore.getState().reset()
    useGenerationStore.getState().reset()
    vi.spyOn(generateApi, 'models').mockResolvedValue([KONTEXT])
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    vi.spyOn(enrichmentApi, 'suggestions').mockResolvedValue([])
    vi.spyOn(assetsApi, 'get').mockImplementation(async (id) =>
      id === 'base1' ? base() : generated()
    )
  })

  it('leads with how it was made', async () => {
    await openGenerateTab()

    const record = within(screen.getByRole('region', { name: 'How this was made' }))
    expect(await record.findByText('FLUX.1 Kontext [pro]')).toBeInTheDocument()
    expect(record.getByText('fal-ai/flux-pro/kontext')).toBeInTheDocument()
    expect(record.getByText('Image → image')).toBeInTheDocument()
    expect(record.getByText('the same fox, at dusk')).toBeInTheDocument()
    expect(record.getByText('1234')).toBeInTheDocument()
    expect(record.getByRole('link', { name: 'Fox photo' })).toHaveAttribute(
      'href',
      '/a/base1'
    )
    // The parameters as sent, catalogue defaults included.
    expect(record.getByText('aspect_ratio')).toBeInTheDocument()
    expect(record.getByText('16:9')).toBeInTheDocument()
    expect(record.getByText('enable_safety_checker')).toBeInTheDocument()
  })

  it('regenerates it as it was', async () => {
    const regenerate = vi.spyOn(generateApi, 'regenerate').mockResolvedValue(queued())
    await openGenerateTab()

    await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }))

    await waitFor(() =>
      expect(regenerate).toHaveBeenCalledWith('g1', { prompt: null, reuse_seed: false })
    )
    expect(await screen.findByText(/follow it under background activity/)).toBeVisible()
  })

  it('regenerates it with the seed fal reported', async () => {
    const regenerate = vi.spyOn(generateApi, 'regenerate').mockResolvedValue(queued())
    await openGenerateTab()

    await userEvent.click(
      screen.getByRole('button', { name: 'Regenerate with the same seed' })
    )

    await waitFor(() =>
      expect(regenerate).toHaveBeenCalledWith('g1', { prompt: null, reuse_seed: true })
    )
  })

  it('offers no same-seed run when fal reported no seed', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(generated({ seed: null }))
    await openGenerateTab()

    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Regenerate with the same seed' })
    ).toBeNull()
  })

  it('offers a same-seed run from the seed it asked for when fal echoed none', async () => {
    // nano-banana-2 takes a seed but reports none back; the server's reuse_seed falls
    // back to the one stored in the parameters, so the button has something to send.
    const regenerate = vi.spyOn(generateApi, 'regenerate').mockResolvedValue(queued())
    vi.spyOn(assetsApi, 'get').mockResolvedValue(
      generated({ seed: null, parameters: { aspect_ratio: '16:9', seed: 42 } })
    )
    await openGenerateTab()

    await userEvent.click(
      screen.getByRole('button', { name: 'Regenerate with the same seed' })
    )

    await waitFor(() =>
      expect(regenerate).toHaveBeenCalledWith('g1', { prompt: null, reuse_seed: true })
    )
  })

  it('leaves out the date of a generation that has none', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(generated({ generated_at: null }))
    await openGenerateTab()

    const record = within(screen.getByRole('region', { name: 'How this was made' }))
    expect(record.getByText('Model')).toBeInTheDocument()
    expect(record.queryByText('Generated')).toBeNull()
  })

  it('says what the server said when a base has gone', async () => {
    const error = new AxiosError('Conflict')
    error.response = {
      status: 409,
      statusText: 'Conflict',
      data: {
        detail: {
          code: 'source_asset_missing',
          message: '“Fox photo” was deleted, so this cannot be regenerated',
        },
      },
      headers: {},
      config: { headers: new AxiosHeaders() },
    }
    vi.spyOn(generateApi, 'regenerate').mockRejectedValue(error)
    await openGenerateTab()

    await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '“Fox photo” was deleted, so this cannot be regenerated'
    )
  })

  it('opens the form pre-filled for Edit and generate', async () => {
    await openGenerateTab()

    await userEvent.click(screen.getByRole('button', { name: 'Edit and generate' }))

    const form = await screen.findByRole('form', { name: 'Edit and generate' })
    expect(within(form).getByLabelText('Prompt')).toHaveValue('the same fox, at dusk')
    expect(within(form).getByLabelText('Model')).toHaveValue('kontext')
    expect(within(form).getByLabelText('Aspect ratio')).toHaveValue('16:9')
    expect(within(form).getByText('Fox photo')).toBeInTheDocument()
    // One form at a time: the "from this image" one steps aside while editing.
    expect(screen.queryByRole('form', { name: 'Generate from this image' })).toBeNull()
  })

  it('names the base that can no longer be opened', async () => {
    vi.spyOn(assetsApi, 'get').mockImplementation(async (id) => {
      if (id === 'base1') throw new Error('Not Found')
      return generated()
    })
    await openGenerateTab()

    await userEvent.click(screen.getByRole('button', { name: 'Edit and generate' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Fox photo/)
    expect(screen.queryByRole('form', { name: 'Edit and generate' })).toBeNull()
  })

  it('offers an ordinary image as a base', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(base())
    await openGenerateTab('/a/base1')

    const form = screen.getByRole('form', { name: 'Generate from this image' })
    expect(within(form).getByText('Base image')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'How this was made' })).toBeNull()
  })

  it('has no Generate tab for a video nobody generated', async () => {
    vi.spyOn(assetsApi, 'get').mockResolvedValue(asset())
    // A video mounts the transcript and clip tabs, which load on mount.
    vi.spyOn(transcriptsApi, 'get').mockResolvedValue({
      asset_id: 'a1',
      status: null,
      model: null,
      language: null,
      segments: [],
    })
    vi.spyOn(clipsApi, 'list').mockResolvedValue([])
    renderAt('/a/a1')
    await screen.findByRole('heading', { name: 'Giordano interview' })

    expect(screen.queryByRole('tab', { name: 'Generate' })).toBeNull()
  })

  it('drops "(est.)" from the cost once it is a provider’s own bill', async () => {
    vi.spyOn(usageApi, 'forAsset').mockResolvedValue({
      total_events: 1,
      priced_events: 1,
      cost: 0.04,
      currency: 'USD',
      estimated: false,
      tokens: 0,
      seconds: 0,
    })
    renderAt('/a/g1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Info' }))

    expect(await screen.findByText('AI cost')).toBeInTheDocument()
    expect(screen.queryByText('AI cost (est.)')).toBeNull()
  })
})
