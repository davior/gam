import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import TagInput from '@/components/TagInput'
import type { Tag } from '@/api/tags'

const tag = (id: string, name: string, asset_count?: number): Tag => ({
  id,
  name,
  category_id: null,
  asset_count,
})

function setup(props: Partial<React.ComponentProps<typeof TagInput>> = {}) {
  const onAdd = vi.fn()
  const onRemove = vi.fn()
  render(
    <TagInput
      tags={props.tags ?? []}
      suggestions={props.suggestions ?? []}
      onAdd={onAdd}
      onRemove={onRemove}
      {...props}
    />
  )
  return { onAdd, onRemove, user: userEvent.setup() }
}

describe('TagInput', () => {
  it('commits a typed name on Enter', async () => {
    const { onAdd, user } = setup()
    await user.type(screen.getByRole('combobox'), 'interview{Enter}')
    expect(onAdd).toHaveBeenCalledWith(['interview'])
  })

  it('commits a suggestion when it is clicked', async () => {
    const { onAdd, user } = setup({ suggestions: [tag('t1', 'NATO', 4)] })
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: /NATO/ }))
    expect(onAdd).toHaveBeenCalledWith(['NATO'])
  })

  it('does not offer a tag that is already attached', async () => {
    const { user } = setup({
      tags: [tag('t1', 'NATO')],
      suggestions: [tag('t1', 'NATO'), tag('t2', 'archive')],
    })
    await user.click(screen.getByRole('combobox'))
    expect(await screen.findByRole('option', { name: /archive/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /NATO/ })).not.toBeInTheDocument()
  })

  it('does not fire a request for a tag that differs only in case', async () => {
    /**
     * The server folds "nato" onto the existing "NATO" and returns the same set, so the
     * request succeeds and nothing visibly happens — which reads as a control that
     * swallowed your input. Caught here instead.
     */
    const { onAdd, user } = setup({ tags: [tag('t1', 'NATO')] })
    await user.type(screen.getByRole('combobox'), 'nato{Enter}')
    expect(onAdd).not.toHaveBeenCalled()
  })

  it('collapses stray whitespace so what is sent is what comes back', async () => {
    const { onAdd, user } = setup()
    await user.type(screen.getByRole('combobox'), '  klaus   schwab  {Enter}')
    expect(onAdd).toHaveBeenCalledWith(['klaus schwab'])
  })

  it('ignores a blank commit', async () => {
    const { onAdd, user } = setup()
    await user.type(screen.getByRole('combobox'), '   {Enter}')
    expect(onAdd).not.toHaveBeenCalled()
  })

  it('removes the last token on Backspace in an empty input', async () => {
    const { onRemove, user } = setup({ tags: [tag('t1', 'first'), tag('t2', 'last')] })
    await user.click(screen.getByRole('combobox'))
    await user.keyboard('{Backspace}')
    expect(onRemove).toHaveBeenCalledWith('t2')
  })

  it('does not remove a token when there is text to delete', async () => {
    const { onRemove, user } = setup({ tags: [tag('t1', 'keep')] })
    await user.type(screen.getByRole('combobox'), 'ab{Backspace}')
    expect(onRemove).not.toHaveBeenCalled()
  })

  it('removes a tag from its ✕', async () => {
    const { onRemove, user } = setup({ tags: [tag('t1', 'NATO')] })
    await user.click(screen.getByRole('button', { name: 'Remove NATO' }))
    expect(onRemove).toHaveBeenCalledWith('t1')
  })

  it('commits the highlighted suggestion when the keyboard drives the menu', async () => {
    const { onAdd, user } = setup({
      suggestions: [tag('t1', 'alpha'), tag('t2', 'beta')],
    })
    await user.click(screen.getByRole('combobox'))
    await user.keyboard('{ArrowDown}{Enter}')
    expect(onAdd).toHaveBeenCalledWith(['beta'])
  })
})
