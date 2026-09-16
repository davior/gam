import { useEffect, useMemo, useState } from 'react'
import { Check, FolderPlus, Pencil, Plus, Tags, Trash2, X } from 'lucide-react'
import { buildCategoryTree, type CategoryNode, type Tag } from '@/api/tags'
import { useTagStore } from '@/stores/tags'

/**
 * Managing the vocabulary itself, rather than which assets carry it.
 *
 * M3 built a tag API with rename, delete and a nested category tree, and then wired a
 * picker that can only ever *add*. A user could type a tag into existence and then never
 * rename, delete or file it — the recursive-CTE tree was, from the UI, read-only.
 *
 * In Settings rather than at its own route: this is housekeeping, done rarely, and the
 * header already carries five controls.
 */

const UNFILED = '__unfiled__'

export default function TagPanel() {
  const tags = useTagStore((s) => s.tags)
  const categories = useTagStore((s) => s.categories)
  const loading = useTagStore((s) => s.loading)
  const storeError = useTagStore((s) => s.error)
  const ensureLoaded = useTagStore((s) => s.ensureLoaded)
  const createTag = useTagStore((s) => s.create)
  const renameTag = useTagStore((s) => s.rename)
  const recategorise = useTagStore((s) => s.recategorise)
  const removeTag = useTagStore((s) => s.remove)
  const createCategory = useTagStore((s) => s.createCategory)
  const updateCategory = useTagStore((s) => s.updateCategory)
  const removeCategory = useTagStore((s) => s.removeCategory)

  const [newTag, setNewTag] = useState('')
  const [newCategory, setNewCategory] = useState('')
  const [editingTag, setEditingTag] = useState<string | null>(null)
  const [editingCategory, setEditingCategory] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  useEffect(() => {
    ensureLoaded()
  }, [ensureLoaded])

  const tree = useMemo(() => buildCategoryTree(categories), [categories])
  // Flattened with a depth, so the category select can show the nesting the tree
  // encodes. Same shape FilterBar uses, for the same reason.
  const options = useMemo(() => flatten(tree), [tree])

  const grouped = useMemo(() => {
    const byCategory = new Map<string, Tag[]>()
    tags.forEach((tag) => {
      const key = tag.category_id ?? UNFILED
      byCategory.set(key, [...(byCategory.get(key) ?? []), tag])
    })
    return byCategory
  }, [tags])

  const submitTag = async () => {
    const name = newTag.trim()
    if (!name) return
    setNewTag('')
    try {
      await createTag(name, null)
    } catch {
      /* the store holds the sentence */
    }
  }

  const submitCategory = async () => {
    const name = newCategory.trim()
    if (!name) return
    setNewCategory('')
    try {
      await createCategory(name, null)
    } catch {
      /* the store holds the sentence */
    }
  }

  const commitRename = async (tag: Tag) => {
    const name = draft.trim()
    setEditingTag(null)
    if (!name || name === tag.name) return
    try {
      await renameTag(tag.id, name)
    } catch {
      /* the store holds the sentence */
    }
  }

  const commitCategoryRename = async (node: CategoryNode) => {
    const name = draft.trim()
    setEditingCategory(null)
    if (!name || name === node.name) return
    try {
      await updateCategory(node.id, { name })
    } catch {
      /* the store holds the sentence */
    }
  }

  return (
    <section className="card space-y-4 p-5">
      <header className="flex items-center gap-2">
        <Tags className="h-4 w-4 text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Tags</h2>
      </header>

      <p className="text-xs text-gray-600 dark:text-gray-400">
        Rename, delete and file your tags. Deleting a category keeps its tags and
        sub-categories — they move to the top level rather than going with it.
      </p>

      {storeError && (
        <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          {storeError}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <div className="flex min-w-[12rem] flex-1 gap-1">
          <input
            className="input text-xs"
            placeholder="New tag"
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void submitTag()
            }}
            aria-label="New tag"
          />
          <button
            type="button"
            className="btn btn-secondary shrink-0 px-2"
            onClick={() => void submitTag()}
            aria-label="Add tag"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="flex min-w-[12rem] flex-1 gap-1">
          <input
            className="input text-xs"
            placeholder="New category"
            value={newCategory}
            onChange={(e) => setNewCategory(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void submitCategory()
            }}
            aria-label="New category"
          />
          <button
            type="button"
            className="btn btn-secondary shrink-0 px-2"
            onClick={() => void submitCategory()}
            aria-label="Add category"
          >
            <FolderPlus className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {loading && tags.length === 0 && (
        <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
          Loading…
        </p>
      )}

      {!loading && tags.length === 0 && categories.length === 0 && (
        <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
          No tags yet. Tag an asset, or add one above.
        </p>
      )}

      <div className="space-y-3">
        {options.map(({ node, depth }) => (
          <div key={node.id} style={{ marginLeft: depth * 12 }}>
            <div className="flex items-center gap-1.5 border-b border-gray-100 pb-1 dark:border-gray-800">
              {editingCategory === node.id ? (
                <input
                  className="input py-0.5 text-xs"
                  value={draft}
                  autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={() => void commitCategoryRename(node)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void commitCategoryRename(node)
                    if (e.key === 'Escape') setEditingCategory(null)
                  }}
                  aria-label={`Rename ${node.name}`}
                />
              ) : (
                <>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    {node.name}
                  </h3>
                  <button
                    type="button"
                    className="btn btn-ghost p-1"
                    aria-label={`Rename ${node.name}`}
                    onClick={() => {
                      setDraft(node.name)
                      setEditingCategory(node.id)
                    }}
                  >
                    <Pencil className="h-3 w-3" />
                  </button>
                  <select
                    className="input ml-auto w-auto py-0.5 text-xs"
                    value={node.parent_category_id ?? ''}
                    aria-label={`Move ${node.name} into`}
                    onChange={(e) =>
                      void updateCategory(node.id, {
                        parent_category_id: e.target.value || null,
                      }).catch(() => {
                        /* the store holds the sentence */
                      })
                    }
                  >
                    <option value="">Top level</option>
                    {options
                      // A category cannot be its own parent. The server refuses a wider
                      // set than this — anything that would make a cycle — and says so;
                      // this only removes the one case that is obviously nonsense.
                      .filter((o) => o.node.id !== node.id)
                      .map((o) => (
                        <option key={o.node.id} value={o.node.id}>
                          {' '.repeat(o.depth * 2)}
                          {o.node.name}
                        </option>
                      ))}
                  </select>
                  <button
                    type="button"
                    className="btn btn-ghost p-1 text-red-600 dark:text-red-400"
                    aria-label={`Delete ${node.name}`}
                    onClick={() =>
                      void removeCategory(node.id).catch(() => {
                        /* the store holds the sentence */
                      })
                    }
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </>
              )}
            </div>

            <TagRows
              tags={grouped.get(node.id) ?? []}
              options={options}
              editingTag={editingTag}
              draft={draft}
              setDraft={setDraft}
              setEditingTag={setEditingTag}
              commitRename={commitRename}
              recategorise={recategorise}
              removeTag={removeTag}
            />
          </div>
        ))}

        <div>
          <h3 className="border-b border-gray-100 pb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:border-gray-800 dark:text-gray-400">
            Unfiled
          </h3>
          <TagRows
            tags={grouped.get(UNFILED) ?? []}
            options={options}
            editingTag={editingTag}
            draft={draft}
            setDraft={setDraft}
            setEditingTag={setEditingTag}
            commitRename={commitRename}
            recategorise={recategorise}
            removeTag={removeTag}
          />
        </div>
      </div>
    </section>
  )
}

interface RowsProps {
  tags: Tag[]
  options: Array<{ node: CategoryNode; depth: number }>
  editingTag: string | null
  draft: string
  setDraft: (value: string) => void
  setEditingTag: (id: string | null) => void
  commitRename: (tag: Tag) => Promise<void>
  recategorise: (id: string, categoryId: string | null) => Promise<void>
  removeTag: (id: string) => Promise<void>
}

function TagRows({
  tags,
  options,
  editingTag,
  draft,
  setDraft,
  setEditingTag,
  commitRename,
  recategorise,
  removeTag,
}: RowsProps) {
  if (tags.length === 0) {
    return <p className="py-1 text-xs text-gray-400 dark:text-gray-600">No tags here.</p>
  }

  return (
    <ul className="divide-y divide-gray-100 dark:divide-gray-800">
      {tags.map((tag) => (
        <li key={tag.id} className="flex items-center gap-1.5 py-1">
          {editingTag === tag.id ? (
            <>
              <input
                className="input py-0.5 text-xs"
                value={draft}
                autoFocus
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void commitRename(tag)
                  if (e.key === 'Escape') setEditingTag(null)
                }}
                aria-label={`Rename ${tag.name}`}
              />
              <button
                type="button"
                className="btn btn-ghost p-1"
                aria-label="Save"
                onClick={() => void commitRename(tag)}
              >
                <Check className="h-3 w-3" />
              </button>
              <button
                type="button"
                className="btn btn-ghost p-1"
                aria-label="Cancel"
                onClick={() => setEditingTag(null)}
              >
                <X className="h-3 w-3" />
              </button>
            </>
          ) : (
            <>
              <span className="truncate text-xs text-gray-800 dark:text-gray-200">
                {tag.name}
              </span>
              {typeof tag.asset_count === 'number' && (
                <span className="text-xs text-gray-400 dark:text-gray-600">
                  {tag.asset_count}
                </span>
              )}
              <button
                type="button"
                className="btn btn-ghost p-1"
                aria-label={`Rename ${tag.name}`}
                onClick={() => {
                  setDraft(tag.name)
                  setEditingTag(tag.id)
                }}
              >
                <Pencil className="h-3 w-3" />
              </button>
              <select
                className="input ml-auto w-auto py-0.5 text-xs"
                value={tag.category_id ?? ''}
                aria-label={`Category for ${tag.name}`}
                onChange={(e) =>
                  void recategorise(tag.id, e.target.value || null).catch(() => {
                    /* the store holds the sentence */
                  })
                }
              >
                <option value="">Unfiled</option>
                {options.map((o) => (
                  <option key={o.node.id} value={o.node.id}>
                    {' '.repeat(o.depth * 2)}
                    {o.node.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-ghost p-1 text-red-600 dark:text-red-400"
                aria-label={`Delete ${tag.name}`}
                onClick={() =>
                  void removeTag(tag.id).catch(() => {
                    /* the store holds the sentence */
                  })
                }
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </>
          )}
        </li>
      ))}
    </ul>
  )
}

function flatten(
  nodes: CategoryNode[],
  depth = 0
): Array<{ node: CategoryNode; depth: number }> {
  return nodes.flatMap((node) => [{ node, depth }, ...flatten(node.children, depth + 1)])
}
