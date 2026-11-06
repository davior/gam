import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { playlistsApi } from '@/api/playlists'
import { usePlaylistStore } from '@/stores/playlists'
import { makePlaylist, makePlaylistItem } from '@/test-fixtures'

beforeEach(() => {
  usePlaylistStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('usePlaylistStore', () => {
  it('takes the open playlist from every write, and keeps the list in step', async () => {
    const one = makePlaylistItem({ id: 'i1' })
    const two = makePlaylistItem({ id: 'i2', asset_id: 'v2' })
    vi.spyOn(playlistsApi, 'get').mockResolvedValue(makePlaylist([one, two]))
    const reorder = vi
      .spyOn(playlistsApi, 'reorder')
      .mockResolvedValue(makePlaylist([two, one], { name: 'Moved' }))

    await usePlaylistStore.getState().open('p1')
    await usePlaylistStore.getState().move('i2', -1)

    expect(reorder).toHaveBeenCalledWith('p1', ['i2', 'i1'])
    const state = usePlaylistStore.getState()
    expect(state.current?.items.map((i) => i.id)).toEqual(['i2', 'i1'])
    expect(state.playlists.map((p) => p.name)).toEqual(['Moved'])
    expect(state.playlists[0]).not.toHaveProperty('items')
  })

  it('does not move past either end', async () => {
    vi.spyOn(playlistsApi, 'get').mockResolvedValue(makePlaylist([makePlaylistItem()]))
    const reorder = vi.spyOn(playlistsApi, 'reorder')
    await usePlaylistStore.getState().open('p1')
    await usePlaylistStore.getState().move('i1', -1)
    await usePlaylistStore.getState().move('i1', 1)
    expect(reorder).not.toHaveBeenCalled()
  })

  it('drops a slow answer for a playlist that is no longer open', async () => {
    let finish: (value: ReturnType<typeof makePlaylist>) => void = () => {}
    vi.spyOn(playlistsApi, 'get')
      .mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)))
      .mockResolvedValueOnce(makePlaylist([], { id: 'p2', name: 'Second' }))

    const first = usePlaylistStore.getState().open('p1')
    await usePlaylistStore.getState().open('p2')
    finish(makePlaylist([], { id: 'p1', name: 'First' }))
    await first

    expect(usePlaylistStore.getState().current?.name).toBe('Second')
  })

  it('reports a refused edit and keeps what the server last said', async () => {
    vi.spyOn(playlistsApi, 'get').mockResolvedValue(makePlaylist([makePlaylistItem()]))
    vi.spyOn(playlistsApi, 'updateItem').mockRejectedValue(new Error('nope'))
    await usePlaylistStore.getState().open('p1')
    await expect(
      usePlaylistStore.getState().updateItem('i1', { in_point: 5, out_point: 1 })
    ).rejects.toThrow()
    const state = usePlaylistStore.getState()
    expect(state.currentError).toBeTruthy()
    expect(state.current?.items[0].in_point).toBeNull()
  })
})
