import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PlaylistPlayer from '@/components/PlaylistPlayer'
import type { PlaylistItem } from '@/api/playlists'
import { makePlaylistItem } from '@/test-fixtures'

/** The player with its index held as the page holds it. */
function Harness({ items, hold = false }: { items: PlaylistItem[]; hold?: boolean }) {
  const [index, setIndex] = useState(0)
  return (
    <>
      <span data-testid="index">{index}</span>
      <PlaylistPlayer items={items} index={index} onIndexChange={setIndex} hold={hold} />
    </>
  )
}

function media(container: HTMLElement) {
  return container.querySelector('video, audio') as HTMLMediaElement
}

function at(container: HTMLElement, seconds: number) {
  const element = media(container)
  Object.defineProperty(element, 'currentTime', { value: seconds, writable: true })
  fireEvent.timeUpdate(element)
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('PlaylistPlayer', () => {
  it('starts a range at its in point and moves on at its out point', () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    const items = [
      makePlaylistItem({ id: 'a', in_point: 10, out_point: 20, start: 10, end: 20 }),
      makePlaylistItem({
        id: 'b',
        asset_type: 'audio',
        asset_name: 'Talk',
        start: 5,
        end: 9,
      }),
    ]
    const { container } = render(<Harness items={items} />)
    fireEvent.loadedMetadata(media(container))
    expect(media(container).currentTime).toBe(10)

    fireEvent.play(media(container))
    at(container, 15)
    expect(screen.getByTestId('index')).toHaveTextContent('0')
    at(container, 20)
    expect(screen.getByTestId('index')).toHaveTextContent('1')

    // The next one starts by itself, at its own start, now the person has pressed play.
    const audio = media(container)
    expect(audio.tagName).toBe('AUDIO')
    fireEvent.loadedMetadata(audio)
    expect(audio.currentTime).toBe(5)
    expect(play).toHaveBeenCalled()
  })

  it('moves on when a whole file ends, and stops after the last', () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const items = [makePlaylistItem({ id: 'a' }), makePlaylistItem({ id: 'b' })]
    const { container } = render(<Harness items={items} />)
    fireEvent.ended(media(container))
    expect(screen.getByTestId('index')).toHaveTextContent('1')
    fireEvent.ended(media(container))
    expect(screen.getByTestId('index')).toHaveTextContent('1')
  })

  it('shows an image for its seconds once playing', () => {
    vi.useFakeTimers()
    const items = [
      makePlaylistItem({ id: 'a', asset_type: 'image', start: 0, end: 3 }),
      makePlaylistItem({ id: 'b' }),
    ]
    render(<Harness items={items} />)
    // Not before the person starts it.
    act(() => vi.advanceTimersByTime(10_000))
    expect(screen.getByTestId('index')).toHaveTextContent('0')

    fireEvent.click(screen.getByRole('button', { name: /play the playlist from here/i }))
    act(() => vi.advanceTimersByTime(2_900))
    expect(screen.getByTestId('index')).toHaveTextContent('0')
    act(() => vi.advanceTimersByTime(200))
    expect(screen.getByTestId('index')).toHaveTextContent('1')
  })

  it('stays on the item while its range is being edited', () => {
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    const items = [
      makePlaylistItem({ id: 'a', in_point: 0, out_point: 2, start: 0, end: 2 }),
      makePlaylistItem({ id: 'b' }),
    ]
    const { container } = render(<Harness items={items} hold />)
    at(container, 5)
    fireEvent.ended(media(container))
    expect(screen.getByTestId('index')).toHaveTextContent('0')
  })

  it('steps with previous and next, and says what is playing', () => {
    const items = [
      makePlaylistItem({ id: 'a', label: 'On the bailout' }),
      makePlaylistItem({ id: 'b', asset_name: 'Panel' }),
    ]
    render(<Harness items={items} />)
    expect(screen.getByText('On the bailout')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /previous item/i })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /next item/i }))
    expect(screen.getByText('Panel')).toBeInTheDocument()
    expect(screen.getByText(/2 of 2/)).toBeInTheDocument()
  })

  it('says so when the playlist is empty', () => {
    render(<Harness items={[]} />)
    expect(screen.getByText(/this playlist is empty/i)).toBeInTheDocument()
  })
})
