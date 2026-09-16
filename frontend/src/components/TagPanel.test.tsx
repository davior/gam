import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TagPanel from '@/components/TagPanel'
import { tagsApi, type Tag, type TagCategory } from '@/api/tags'
import { useTagStore } from '@/stores/tags'

function tag(overrides: Partial<Tag> = {}): Tag {
  return { id: 't1', name: 'interview', category_id: null, asset_count: 3, ...overrides }
}

function category(overrides: Partial<TagCategory> = {}): TagCategory {
  return { id: 'c1', name: 'Formats', parent_category_id: null, ...overrides }
}

beforeEach(() => {
  useTagStore.getState().reset()
  vi.spyOn(tagsApi, 'list').mockResolvedValue([tag()])
  vi.spyOn(tagsApi, 'listCategories').mockResolvedValue([category()])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('TagPanel', () => {
  it('lists the vocabulary, grouped by category', async () => {
    render(<TagPanel />)

    expect(await screen.findByText('interview')).toBeInTheDocument()
    // By role: the category name is also an <option> in every move select, so a bare
    // text query matches several nodes.
    expect(screen.getByRole('heading', { name: 'Formats' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Unfiled' })).toBeInTheDocument()
  })

  it('renames a tag', async () => {
    const rename = vi
      .spyOn(tagsApi, 'rename')
      .mockResolvedValue(tag({ name: 'long interview' }))
    render(<TagPanel />)
    await screen.findByText('interview')

    await userEvent.click(screen.getByRole('button', { name: 'Rename interview' }))
    const field = screen.getByRole('textbox', { name: 'Rename interview' })
    await userEvent.clear(field)
    await userEvent.type(field, 'long interview{Enter}')

    await waitFor(() => expect(rename).toHaveBeenCalledWith('t1', 'long interview'))
  })

  it('files a tag under a category', async () => {
    const move = vi
      .spyOn(tagsApi, 'recategorise')
      .mockResolvedValue(tag({ category_id: 'c1' }))
    render(<TagPanel />)
    await screen.findByText('interview')

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Category for interview' }),
      'c1'
    )

    await waitFor(() => expect(move).toHaveBeenCalledWith('t1', 'c1'))
  })

  it('deletes a tag', async () => {
    const remove = vi.spyOn(tagsApi, 'remove').mockResolvedValue(undefined)
    render(<TagPanel />)
    await screen.findByText('interview')

    await userEvent.click(screen.getByRole('button', { name: 'Delete interview' }))

    await waitFor(() => expect(remove).toHaveBeenCalledWith('t1'))
  })

  it('creates a tag', async () => {
    const create = vi
      .spyOn(tagsApi, 'create')
      .mockResolvedValue(tag({ id: 't2', name: 'talk' }))
    render(<TagPanel />)
    await screen.findByText('interview')

    await userEvent.type(screen.getByRole('textbox', { name: 'New tag' }), 'talk{Enter}')

    await waitFor(() => expect(create).toHaveBeenCalledWith('talk', null))
  })

  it('creates a category', async () => {
    const create = vi
      .spyOn(tagsApi, 'createCategory')
      .mockResolvedValue(category({ id: 'c2', name: 'Topics' }))
    render(<TagPanel />)
    await screen.findByText('interview')

    await userEvent.type(
      screen.getByRole('textbox', { name: 'New category' }),
      'Topics{Enter}'
    )

    await waitFor(() => expect(create).toHaveBeenCalledWith('Topics', null))
  })

  it('shows the server sentence when a rename collides', async () => {
    render(<TagPanel />)
    await screen.findByText('interview')
    // The backend answers a duplicate with 400 and a sentence worth reading; swallowing
    // it would leave the name silently reverting with no explanation.
    useTagStore.setState({ error: 'You already have a tag called talk' })

    expect(
      await screen.findByText('You already have a tag called talk')
    ).toBeInTheDocument()
  })

  it('says a category delete keeps its contents, because it does', async () => {
    render(<TagPanel />)
    await screen.findByText('interview')

    expect(screen.getByText(/move to the top level/i)).toBeInTheDocument()
  })
})
