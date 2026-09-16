import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { tagsApi, type Tag, type TagCategory } from '@/api/tags'
import { useTagStore } from '@/stores/tags'

function tag(overrides: Partial<Tag> = {}): Tag {
  return { id: 't1', name: 'interview', category_id: null, ...overrides }
}

function category(overrides: Partial<TagCategory> = {}): TagCategory {
  return { id: 'c1', name: 'Formats', parent_category_id: null, ...overrides }
}

beforeEach(() => {
  useTagStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useTagStore', () => {
  it('creates a tag and folds it into the catalogue', async () => {
    vi.spyOn(tagsApi, 'create').mockResolvedValue(tag({ id: 't9', name: 'neuroweapons' }))

    await useTagStore.getState().create('neuroweapons', null)

    expect(useTagStore.getState().tags.map((t) => t.name)).toEqual(['neuroweapons'])
  })

  it('does not duplicate a tag the get-or-create endpoint handed back', async () => {
    useTagStore.setState({ tags: [tag()] })
    vi.spyOn(tagsApi, 'create').mockResolvedValue(tag())

    await useTagStore.getState().create('Interview', null)

    expect(useTagStore.getState().tags).toHaveLength(1)
  })

  it('puts an optimistic rename back when the name is taken', async () => {
    useTagStore.setState({ tags: [tag()] })
    vi.spyOn(tagsApi, 'rename').mockRejectedValue(new Error('taken'))

    await expect(useTagStore.getState().rename('t1', 'talk')).rejects.toThrow()

    expect(useTagStore.getState().tags[0].name).toBe('interview')
    expect(useTagStore.getState().error).toBeTruthy()
  })

  it('moves a tag into a category', async () => {
    useTagStore.setState({ tags: [tag()] })
    vi.spyOn(tagsApi, 'recategorise').mockResolvedValue(tag({ category_id: 'c1' }))

    await useTagStore.getState().recategorise('t1', 'c1')

    expect(useTagStore.getState().tags[0].category_id).toBe('c1')
  })

  it('puts a refused category move back, so the tree never renders a cycle', async () => {
    useTagStore.setState({
      categories: [category(), category({ id: 'c2', name: 'Talks' })],
    })
    vi.spyOn(tagsApi, 'updateCategory').mockRejectedValue(new Error('cycle'))

    await expect(
      useTagStore.getState().updateCategory('c1', { parent_category_id: 'c2' })
    ).rejects.toThrow()

    expect(useTagStore.getState().categories[0].parent_category_id).toBeNull()
  })

  it('reloads after deleting a category, because the server lifts its children', async () => {
    useTagStore.setState({ categories: [category()] })
    vi.spyOn(tagsApi, 'removeCategory').mockResolvedValue(undefined)
    const list = vi.spyOn(tagsApi, 'list').mockResolvedValue([])
    vi.spyOn(tagsApi, 'listCategories').mockResolvedValue([])

    await useTagStore.getState().removeCategory('c1')

    expect(list).toHaveBeenCalled()
  })

  it('does not repopulate a store that was reset while a request was in flight', async () => {
    // Sign-out empties the store. A rejection landing afterwards used to put the
    // previous user's vocabulary back into it.
    useTagStore.setState({ tags: [tag()] })
    let reject: (reason: Error) => void = () => {}
    vi.spyOn(tagsApi, 'remove').mockReturnValue(
      new Promise((_, r) => {
        reject = r
      })
    )

    const pending = useTagStore.getState().remove('t1')
    useTagStore.getState().reset()
    reject(new Error('gone'))
    await pending

    expect(useTagStore.getState().tags).toEqual([])
  })
})
