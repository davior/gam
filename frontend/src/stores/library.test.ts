import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from 'vitest'
import {
  assetsApi,
  type Asset,
  type AssetPage,
  type ListAssetsParams,
} from '@/api/assets'
import { tagsApi, type Tag } from '@/api/tags'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'

const tag = (id: string, name: string): Tag => ({ id, name, category_id: null })

function makeAsset(id: string, tags: Tag[] = []): Asset {
  return {
    id,
    name: id,
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

const page = (data: Asset[], total = data.length): AssetPage => ({
  data,
  total,
  limit: 60,
  offset: 0,
})

let list: MockInstance<(params?: ListAssetsParams) => Promise<AssetPage>>

beforeEach(() => {
  list = vi.spyOn(assetsApi, 'list').mockResolvedValue(page([]))
  useLibraryStore.getState().reset()
  useTagStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

const lastParams = (): ListAssetsParams => {
  const calls = list.mock.calls
  return calls[calls.length - 1][0] ?? {}
}

describe('filters', () => {
  it('sends every filter under the name the API uses', async () => {
    const store = useLibraryStore.getState()
    store.setQuery('nato')
    store.setTypeFilter('video')
    store.toggleTag('interview')
    store.setCategoryFilter('c1')
    store.setSourceFilter('ai_generated')
    store.setDurationRange(60, 600)
    store.setUploadedRange('2026-01-01', '2026-02-01')
    await useLibraryStore.getState().load()

    expect(lastParams()).toMatchObject({
      q: 'nato',
      asset_type: 'video',
      tag: ['interview'],
      category_id: 'c1',
      source: 'ai_generated',
      min_duration: 60,
      max_duration: 600,
      uploaded_after: '2026-01-01',
      uploaded_before: '2026-02-01',
    })
  })

  it('omits a filter that is not set rather than sending null', async () => {
    await useLibraryStore.getState().load()
    const params = lastParams()
    expect(params.tag).toBeUndefined()
    expect(params.source).toBeUndefined()
    expect(params.min_duration).toBeUndefined()
    expect(params).not.toHaveProperty('source', null)
  })

  it('sends page two with the same filters as page one', async () => {
    /**
     * The reason `load` and `loadMore` share one params builder. When each assembled
     * its own, a filter added to one and missed in the other applied to the first page
     * and quietly stopped applying to the second — the grid appearing to invent rows
     * as you scrolled.
     */
    list.mockResolvedValue(page([makeAsset('a1')], 2))
    const store = useLibraryStore.getState()
    store.setSourceFilter('ai_generated')
    store.toggleTag('interview')
    await useLibraryStore.getState().load()
    const first = lastParams()

    list.mockResolvedValue(page([makeAsset('a2')], 2))
    await useLibraryStore.getState().loadMore()
    const second = lastParams()

    expect(second).toMatchObject({ source: 'ai_generated', tag: ['interview'] })
    expect({ ...second, offset: undefined }).toEqual({ ...first, offset: undefined })
    expect(second.offset).toBe(1)
  })

  it('toggles a tag off, and treats case as the server does', async () => {
    const store = useLibraryStore.getState()
    store.toggleTag('NATO')
    expect(useLibraryStore.getState().tagFilter).toEqual(['NATO'])
    // The same tag typed differently must clear the chip, not stack a second filter
    // that ANDs to the same set and cannot be cleared by either ✕.
    store.toggleTag('nato')
    expect(useLibraryStore.getState().tagFilter).toEqual([])
  })

  it('clears every filter at once', async () => {
    const store = useLibraryStore.getState()
    store.setQuery('nato')
    store.setTypeFilter('video')
    store.toggleTag('interview')
    store.setCategoryFilter('c1')
    store.setSourceFilter('ai_generated')
    store.setDurationRange(60, 600)
    store.setUploadedRange('2026-01-01', '2026-02-01')

    useLibraryStore.getState().clearFilters()
    const state = useLibraryStore.getState()
    expect({
      query: state.query,
      typeFilter: state.typeFilter,
      tagFilter: state.tagFilter,
      categoryFilter: state.categoryFilter,
      sourceFilter: state.sourceFilter,
      minDuration: state.minDuration,
      maxDuration: state.maxDuration,
      uploadedAfter: state.uploadedAfter,
      uploadedBefore: state.uploadedBefore,
    }).toEqual({
      query: '',
      typeFilter: null,
      tagFilter: [],
      categoryFilter: null,
      sourceFilter: null,
      minDuration: null,
      maxDuration: null,
      uploadedAfter: null,
      uploadedBefore: null,
    })
  })

  it('leaves no filter behind on reset', () => {
    // A filter that survives `reset()` survives signing out, and the next person sees a
    // library narrowed by someone else's choices with no visible reason.
    const store = useLibraryStore.getState()
    store.setQuery('nato')
    store.setTypeFilter('video')
    store.toggleTag('interview')
    store.setCategoryFilter('c1')
    store.setSourceFilter('ai_generated')
    store.setDurationRange(60, 600)
    store.setUploadedRange('2026-01-01', '2026-02-01')

    useLibraryStore.getState().reset()
    const state = useLibraryStore.getState() as unknown as Record<string, unknown>
    const filterKeys = [
      'query',
      'typeFilter',
      'tagFilter',
      'categoryFilter',
      'sourceFilter',
      'minDuration',
      'maxDuration',
      'uploadedAfter',
      'uploadedBefore',
    ]
    const left = filterKeys.filter((key) => {
      const value = state[key]
      return !(
        value === null ||
        value === '' ||
        (Array.isArray(value) && value.length === 0)
      )
    })
    expect(left).toEqual([])
  })

  it('discards a slow early response that lands after a fast later one', async () => {
    let releaseFirst: (value: AssetPage) => void = () => {}
    list.mockImplementationOnce(
      () => new Promise<AssetPage>((resolve) => (releaseFirst = resolve))
    )
    const slow = useLibraryStore.getState().load()

    list.mockResolvedValue(page([makeAsset('current')]))
    await useLibraryStore.getState().load()

    releaseFirst(page([makeAsset('stale')]))
    await slow

    expect(useLibraryStore.getState().assets.map((a) => a.id)).toEqual(['current'])
  })
})

describe('applyTags', () => {
  let bulk: MockInstance<typeof tagsApi.bulk>

  beforeEach(() => {
    bulk = vi.spyOn(tagsApi, 'bulk').mockResolvedValue({ updated: 0, tags_added: [] })
  })

  it('splits a selection larger than the server accepts', async () => {
    /**
     * `MAX_BULK_ASSETS` is 500. A selection of 600 sent whole is a 422 and no tags
     * applied at all — and 600 is reachable in the grid after ten pages.
     */
    const ids = Array.from({ length: 600 }, (_, i) => `a${i}`)
    await useLibraryStore.getState().applyTags(ids, ['keep'], [])

    expect(bulk).toHaveBeenCalledTimes(2)
    expect(bulk.mock.calls[0][0]).toHaveLength(500)
    expect(bulk.mock.calls[1][0]).toHaveLength(100)
    expect([...bulk.mock.calls[0][0], ...bulk.mock.calls[1][0]]).toEqual(ids)
  })

  it('sends nothing when there is nothing to do', async () => {
    await useLibraryStore.getState().applyTags(['a1'], [], [])
    await useLibraryStore.getState().applyTags([], ['keep'], [])
    expect(bulk).not.toHaveBeenCalled()
  })

  it('patches the rows on screen without a refetch', async () => {
    const added = tag('t9', 'keep')
    bulk.mockResolvedValue({ updated: 2, tags_added: [added] })
    useLibraryStore.setState({ assets: [makeAsset('a1'), makeAsset('a2')], total: 2 })
    const before = list.mock.calls.length

    await useLibraryStore.getState().applyTags(['a1'], ['keep'], [])

    const assets = useLibraryStore.getState().assets
    expect(assets[0].tags.map((t) => t.name)).toEqual(['keep'])
    // Untouched rows stay untouched.
    expect(assets[1].tags).toEqual([])
    expect(list.mock.calls.length).toBe(before)
  })

  it('applies adds before removes, as the server does', async () => {
    const both = tag('t1', 'contested')
    bulk.mockResolvedValue({ updated: 1, tags_added: [both] })
    useLibraryStore.setState({ assets: [makeAsset('a1', [])], total: 1 })

    await useLibraryStore.getState().applyTags(['a1'], ['contested'], ['t1'])

    // The server attaches first and detaches second, so the tag ends up off. Getting
    // this backwards locally would show it on until the next refresh.
    expect(useLibraryStore.getState().assets[0].tags).toEqual([])
  })

  it('refetches when a tag filter is active, because membership may have changed', async () => {
    bulk.mockResolvedValue({ updated: 1, tags_added: [] })
    useLibraryStore.setState({
      assets: [makeAsset('a1', [tag('t1', 'interview')])],
      total: 1,
      tagFilter: ['interview'],
    })
    const before = list.mock.calls.length

    await useLibraryStore.getState().applyTags(['a1'], [], ['t1'])

    expect(list.mock.calls.length).toBeGreaterThan(before)
  })

  it('teaches the tag catalogue about a tag it just created', async () => {
    const created = tag('t9', 'brand new')
    bulk.mockResolvedValue({ updated: 1, tags_added: [created] })
    useLibraryStore.setState({ assets: [makeAsset('a1')], total: 1 })

    await useLibraryStore.getState().applyTags(['a1'], ['brand new'], [])

    expect(useTagStore.getState().tags.map((t) => t.name)).toContain('brand new')
  })

  it('puts the rows back when the call fails', async () => {
    const original = [makeAsset('a1')]
    useLibraryStore.setState({ assets: original, total: 1 })
    bulk.mockRejectedValue(new Error('nope'))

    await expect(
      useLibraryStore.getState().applyTags(['a1'], ['keep'], [])
    ).rejects.toThrow()
    expect(useLibraryStore.getState().assets).toEqual(original)
    expect(useLibraryStore.getState().error).toBeTruthy()
  })
})
