import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PageImport from '@/components/PageImport'
import { importsApi, type PagePreview, type PageTrack } from '@/api/imports'
import type { ActivityJob } from '@/api/transcripts'
import { useActivityStore } from '@/stores/activity'

function track(overrides: Partial<PageTrack> = {}): PageTrack {
  return {
    audio_url: 'https://assets.mixkit.co/music/910/910.mp3',
    title: "I'm Still Waitin'",
    creator: 'Michael Ramir C.',
    publisher: 'Mixkit',
    source_title: null,
    description: null,
    duration_seconds: 158,
    published_date: '2020-01-28',
    license: 'Mixkit Stock Music Free License',
    credit_line: null,
    source_url: null,
    genres: ['Folk Pop'],
    moods: ['Warm', 'Carefree'],
    instruments: ['Acoustic Guitar'],
    tags: ['Road Trip'],
    bpm: null,
    existing_asset_id: null,
    ...overrides,
  }
}

const texas = track({
  audio_url: 'https://assets.mixkit.co/music/1053/1053.mp3',
  title: 'Bigger than Texas',
  genres: ['Country'],
  moods: ['Relaxed'],
  instruments: ['Percussion'],
  bpm: 96,
})
const imported = track({
  audio_url: 'https://assets.mixkit.co/music/903/903.mp3',
  title: 'Farewell to You',
  existing_asset_id: 'a9',
})

function preview(tracks: PageTrack[], overrides: Partial<PagePreview> = {}): PagePreview {
  return {
    page_url: 'https://mixkit.co/free-stock-music/mood/carefree/',
    method: 'structured',
    source: 'mixkit',
    note: '',
    tracks,
    ...overrides,
  }
}

const refresh = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  refresh.mockClear()
  useActivityStore.setState({ refresh })
})

afterEach(() => vi.restoreAllMocks())

async function find(tracks: PageTrack[], overrides: Partial<PagePreview> = {}) {
  const spy = vi
    .spyOn(importsApi, 'previewPage')
    .mockResolvedValue(preview(tracks, overrides))
  render(<PageImport />)
  await userEvent.type(
    screen.getByLabelText('Page of tracks'),
    'https://mixkit.co/free-stock-music/mood/carefree/'
  )
  await userEvent.click(screen.getByRole('button', { name: 'Find tracks' }))
  return spy
}

describe('PageImport', () => {
  it('lists what the page holds, with where the details came from', async () => {
    const spy = await find([track(), texas])

    expect(spy).toHaveBeenCalledWith(
      'https://mixkit.co/free-stock-music/mood/carefree/',
      true
    )
    expect(await screen.findByText(/2 tracks found, from Mixkit/)).toBeInTheDocument()
    expect(
      screen.getByText('Folk Pop, Warm, Carefree, Acoustic Guitar')
    ).toBeInTheDocument()
    expect(screen.getByText('Country, Relaxed, Percussion · 96 BPM')).toBeInTheDocument()
  })

  it('imports only the ticked tracks', async () => {
    const start = vi
      .spyOn(importsApi, 'importTracks')
      .mockResolvedValue([{ id: 'j1' } as ActivityJob])
    await find([track(), texas])

    await userEvent.click(
      await screen.findByRole('checkbox', { name: 'Bigger than Texas' })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Import 1 track' }))

    expect(start).toHaveBeenCalledWith([texas])
    expect(await screen.findByText(/Queued 1 track\./)).toBeInTheDocument()
    expect(refresh).toHaveBeenCalled()
  })

  it('filters by details, and ticks only what is shown', async () => {
    const start = vi.spyOn(importsApi, 'importTracks').mockResolvedValue([])
    await find([track(), texas])

    await userEvent.type(
      await screen.findByLabelText('Filter tracks'),
      'relaxed percussion'
    )
    expect(screen.queryByText("I'm Still Waitin'")).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Tick shown' }))
    await userEvent.click(screen.getByRole('button', { name: 'Import 1 track' }))

    expect(start).toHaveBeenCalledWith([texas])
  })

  it('will not offer a track the library already has', async () => {
    await find([imported])
    expect(
      await screen.findByRole('checkbox', { name: 'Farewell to You' })
    ).toBeDisabled()
    expect(screen.getByText('In library')).toBeInTheDocument()
  })

  it('flags a familiar title but still lets it be chosen, one at a time', async () => {
    const start = vi.spyOn(importsApi, 'importTracks').mockResolvedValue([])
    const familiar = { ...texas, similar_asset_id: 'a5' }
    await find([track(), familiar])

    expect(await screen.findByText('Maybe in library')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Tick shown' }))
    await userEvent.click(screen.getByRole('button', { name: 'Import 1 track' }))
    expect(start).toHaveBeenLastCalledWith([track()])

    await userEvent.click(screen.getByRole('checkbox', { name: 'Bigger than Texas' }))
    expect(screen.getByRole('checkbox', { name: 'Bigger than Texas' })).toBeChecked()
  })

  it('says when the details are only the bare links', async () => {
    await find([track({ genres: [], moods: [], instruments: [] })], {
      method: 'links',
      source: 'links',
      note: 'No AI provider is configured, so these are the bare audio links.',
    })
    expect(await screen.findByText(/No AI provider is configured/)).toBeInTheDocument()
    expect(screen.getByText(/titles only/)).toBeInTheDocument()
  })

  it('skips the AI when asked to', async () => {
    const spy = vi.spyOn(importsApi, 'previewPage').mockResolvedValue(preview([]))
    render(<PageImport />)
    await userEvent.click(screen.getByLabelText(/Ask the AI/))
    await userEvent.type(
      screen.getByLabelText('Page of tracks'),
      'https://example.org/music'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Find tracks' }))
    expect(spy).toHaveBeenCalledWith('https://example.org/music', false)
  })
})
