import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as configModule from '@/api/config'
import { playlistsApi } from '@/api/playlists'
import { usePlaylistStore } from '@/stores/playlists'
import PlaylistView from '@/views/PlaylistView'
import { makePlaylist, makePlaylistItem } from '@/test-fixtures'

function show() {
  return render(
    <MemoryRouter initialEntries={['/playlists/p1']}>
      <Routes>
        <Route path="/playlists/:id" element={<PlaylistView />} />
      </Routes>
    </MemoryRouter>
  )
}

beforeEach(() => {
  usePlaylistStore.getState().reset()
  vi.spyOn(configModule, 'loadConfig').mockResolvedValue({
    read_only: false,
  } as Awaited<ReturnType<typeof configModule.loadConfig>>)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('PlaylistView', () => {
  it('lists the items and edits a range to a stretch of the video', async () => {
    const item = makePlaylistItem({ id: 'i1', label: 'On the bailout' })
    vi.spyOn(playlistsApi, 'get').mockResolvedValue(makePlaylist([item]))
    const update = vi
      .spyOn(playlistsApi, 'updateItem')
      .mockResolvedValue(
        makePlaylist([{ ...item, in_point: 0, out_point: 12, start: 0, end: 12 }])
      )
    show()
    const list = await screen.findByRole('list', { name: /playlist items/i })
    expect(within(list).getByText('Video · whole · 1:00')).toBeInTheDocument()

    await userEvent.click(
      within(list).getByRole('button', { name: /edit on the bailout/i })
    )
    await userEvent.click(screen.getByRole('checkbox', { name: /play the whole video/i }))
    // It opens at the whole video, 0:00 to 1:00.
    const minutes = screen.getByRole('spinbutton', { name: /out point minutes/i })
    await userEvent.clear(minutes)
    await userEvent.type(minutes, '0')
    const seconds = screen.getByRole('spinbutton', { name: /out point seconds/i })
    await userEvent.clear(seconds)
    await userEvent.type(seconds, '12')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(update).toHaveBeenCalledWith('p1', 'i1', {
      label: 'On the bailout',
      in_point: 0,
      out_point: 12,
    })
    await waitFor(() => expect(within(list).getByText(/0:00–0:12/)).toBeInTheDocument())
  })

  it('refuses an out point before the in point without asking the server', async () => {
    vi.spyOn(playlistsApi, 'get').mockResolvedValue(
      makePlaylist([
        makePlaylistItem({ in_point: 10, out_point: 20, start: 10, end: 20 }),
      ])
    )
    const update = vi.spyOn(playlistsApi, 'updateItem')
    show()
    await userEvent.click(await screen.findByRole('button', { name: /edit interview/i }))
    const seconds = screen.getByRole('spinbutton', { name: /out point seconds/i })
    await userEvent.clear(seconds)
    await userEvent.type(seconds, '5')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent(/after the in point/i)
    expect(update).not.toHaveBeenCalled()
  })

  it('removes and reorders items', async () => {
    const a = makePlaylistItem({ id: 'a', label: 'First' })
    const b = makePlaylistItem({ id: 'b', label: 'Second' })
    vi.spyOn(playlistsApi, 'get').mockResolvedValue(makePlaylist([a, b]))
    const reorder = vi
      .spyOn(playlistsApi, 'reorder')
      .mockResolvedValue(makePlaylist([b, a]))
    const remove = vi
      .spyOn(playlistsApi, 'removeItem')
      .mockResolvedValue(makePlaylist([a]))
    show()
    await userEvent.click(await screen.findByRole('button', { name: /move second up/i }))
    expect(reorder).toHaveBeenCalledWith('p1', ['b', 'a'])
    await userEvent.click(screen.getByRole('button', { name: /remove second/i }))
    expect(remove).toHaveBeenCalledWith('p1', 'b')
  })
})
