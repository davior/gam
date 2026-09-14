import { describe, expect, it } from 'vitest'
import { buildCategoryTree, type TagCategory } from '@/api/tags'

const category = (id: string, parent: string | null = null, name = id): TagCategory => ({
  id,
  name,
  parent_category_id: parent,
})

describe('buildCategoryTree', () => {
  it('nests children under their parent', () => {
    const tree = buildCategoryTree([
      category('people'),
      category('scientists', 'people'),
      category('neuro', 'scientists'),
    ])

    expect(tree).toHaveLength(1)
    expect(tree[0].id).toBe('people')
    expect(tree[0].children[0].id).toBe('scientists')
    expect(tree[0].children[0].children[0].id).toBe('neuro')
  })

  it('sorts siblings by name at every level', () => {
    const tree = buildCategoryTree([
      category('root'),
      category('b', 'root', 'Beta'),
      category('a', 'root', 'Alpha'),
    ])
    expect(tree[0].children.map((c) => c.name)).toEqual(['Alpha', 'Beta'])
  })

  it('keeps a category whose parent is missing, as a root', () => {
    // Dropping it would lose every tag filed under it with nothing to show the user.
    const tree = buildCategoryTree([category('orphan', 'deleted-parent')])
    expect(tree.map((c) => c.id)).toEqual(['orphan'])
  })

  it('does not loop forever on a cycle', () => {
    /**
     * The server refuses to create one, so this is the second line of defence — and it
     * is here because the obvious implementation gets it wrong. Marking each node as
     * "placed" is not enough: with A→B→A, A is attached under B and then B under A, each
     * step looking locally correct, and the result renders until the tab dies. Only
     * walking the whole ancestor chain catches it.
     *
     * A cycle has no root, so both nodes surface at the top level: visible and fixable,
     * rather than hidden or fatal.
     */
    const tree = buildCategoryTree([category('a', 'b'), category('b', 'a')])

    expect(tree.map((c) => c.id).sort()).toEqual(['a', 'b'])
    expect(tree[0].children).toEqual([])
    expect(tree[1].children).toEqual([])
  })

  it('does not loop forever on a self-parent', () => {
    const tree = buildCategoryTree([category('a', 'a')])
    expect(tree.map((c) => c.id)).toEqual(['a'])
    expect(tree[0].children).toEqual([])
  })

  it('is empty for no categories', () => {
    expect(buildCategoryTree([])).toEqual([])
  })
})
