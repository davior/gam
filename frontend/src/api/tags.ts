import client from '@/api/client'

/**
 * The tag catalogue.
 *
 * Tags are attached by *name* rather than by id. The tag box does not know whether what
 * was typed exists yet, and making it resolve that first turns one interaction into two
 * round trips and a race where two tabs both create the same tag.
 */

export interface Tag {
  id: string
  name: string
  category_id: string | null
  /** Only present on the catalogue listing; absent when a tag is read off an asset. */
  asset_count?: number
}

export interface TagCategory {
  id: string
  name: string
  parent_category_id: string | null
}

export interface BulkTagsResult {
  /** What was actually touched — ids belonging to someone else are dropped server-side. */
  updated: number
  tags_added: Tag[]
}

interface ListResponse<T> {
  data: T[]
  total: number
}

interface DataResponse<T> {
  data: T
}

export const tagsApi = {
  list(): Promise<Tag[]> {
    return client.get<ListResponse<Tag>>('/tags').then((r) => r.data.data)
  },

  create(name: string, categoryId?: string | null): Promise<Tag> {
    return client
      .post<DataResponse<Tag>>('/tags', { name, category_id: categoryId ?? null })
      .then((r) => r.data.data)
  },

  rename(id: string, name: string): Promise<Tag> {
    return client
      .patch<DataResponse<Tag>>(`/tags/${id}`, { name })
      .then((r) => r.data.data)
  },

  recategorise(id: string, categoryId: string | null): Promise<Tag> {
    return client
      .patch<DataResponse<Tag>>(`/tags/${id}`, { category_id: categoryId })
      .then((r) => r.data.data)
  },

  remove(id: string): Promise<void> {
    return client.delete(`/tags/${id}`).then(() => undefined)
  },

  listCategories(): Promise<TagCategory[]> {
    return client
      .get<ListResponse<TagCategory>>('/tags/categories/all')
      .then((r) => r.data.data)
  },

  createCategory(name: string, parentId?: string | null): Promise<TagCategory> {
    return client
      .post<DataResponse<TagCategory>>('/tags/categories', {
        name,
        parent_category_id: parentId ?? null,
      })
      .then((r) => r.data.data)
  },

  updateCategory(
    id: string,
    changes: { name?: string; parent_category_id?: string | null }
  ): Promise<TagCategory> {
    return client
      .patch<DataResponse<TagCategory>>(`/tags/categories/${id}`, changes)
      .then((r) => r.data.data)
  },

  removeCategory(id: string): Promise<void> {
    return client.delete(`/tags/categories/${id}`).then(() => undefined)
  },

  /** Attach tags to one asset. Returns the asset's full tag set, not just the additions. */
  addToAsset(assetId: string, names: string[]): Promise<Tag[]> {
    return client
      .post<ListResponse<Tag>>(`/assets/${assetId}/tags`, { names })
      .then((r) => r.data.data)
  },

  removeFromAsset(assetId: string, tagId: string): Promise<Tag[]> {
    return client
      .delete<ListResponse<Tag>>(`/assets/${assetId}/tags/${tagId}`)
      .then((r) => r.data.data)
  },

  bulk(assetIds: string[], add: string[], remove: string[]): Promise<BulkTagsResult> {
    return client
      .post<DataResponse<BulkTagsResult>>('/assets/tags/bulk', {
        asset_ids: assetIds,
        add,
        remove,
      })
      .then((r) => r.data.data)
  },
}

/**
 * Arrange a flat category list into the tree the API deliberately does not send.
 *
 * The server returns it flat because the UI needs both shapes — a tree to browse and a
 * flat list to pick a parent from — and one is derivable from the other.
 *
 * Orphans are treated as roots rather than dropped. A category whose parent is missing
 * is a bug somewhere, but silently hiding it means the user loses tags they filed under
 * it with no way to notice.
 */
export interface CategoryNode extends TagCategory {
  children: CategoryNode[]
}

export function buildCategoryTree(categories: TagCategory[]): CategoryNode[] {
  const byId = new Map<string, CategoryNode>()
  categories.forEach((c) => byId.set(c.id, { ...c, children: [] }))

  /**
   * Whether following this node's parents terminates.
   *
   * The server refuses to create a cycle, so this should always be true. But "should"
   * is doing a lot of work for a failure that renders as an infinite tree and takes the
   * tab with it, and a marker on each node is not enough — with A→B→A, A gets attached
   * under B *and* B under A, each step looking locally fine. Only walking the whole
   * chain catches it.
   */
  const reachesRoot = (node: CategoryNode): boolean => {
    const seen = new Set<string>([node.id])
    let current = node.parent_category_id
    while (current) {
      if (seen.has(current)) return false
      seen.add(current)
      const parent = byId.get(current)
      if (!parent) return true // parent is missing, not looping — handled below
      current = parent.parent_category_id
    }
    return true
  }

  const roots: CategoryNode[] = []
  byId.forEach((node) => {
    const parent = node.parent_category_id ? byId.get(node.parent_category_id) : undefined
    // Orphans become roots rather than disappearing. A category whose parent is missing
    // is a bug somewhere, but hiding it loses every tag filed under it with no way for
    // the user to notice.
    if (parent && reachesRoot(node)) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  })

  const sort = (nodes: CategoryNode[]): CategoryNode[] => {
    nodes.sort((a, b) => a.name.localeCompare(b.name))
    nodes.forEach((n) => sort(n.children))
    return nodes
  }
  return sort(roots)
}
