import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SavedPageImport from '@/components/SavedPageImport'
import { importsApi, type SavedPagePreview, type SavedPageTrack } from '@/api/imports'
import { useLibraryStore } from '@/stores/library'
import { matchFiles } from '@/utils/matchFiles'

function track(overrides: Partial<SavedPageTrack> = {}): SavedPageTrack {
  return {
    audio_url: null,
    title: 'Investigation',
    creator: 'leberch',
    publisher: 'Pixabay',
    source_title: null,
    description: null,
    duration_seconds: 264,
    published_date: null,
    license: null,
    credit_line: null,
    source_url: 'https://pixabay.com/music/crime-scene-investigation-604094/',
    genres: ['Crime Scene'],
    moods: ['Mysterious'],
    instruments: [],
    tags: ['Investigation'],
    bpm: null,
    match_id: '604094',
    ...overrides,
  }
}

const riser = track({ title: 'Riser', match_id: '590947' })
const tensionRiser = track({ title: 'Tension Riser', match_id: '590425' })

function preview(tracks: SavedPageTrack[]): SavedPagePreview {
  return {
    page_url: 'https://pixabay.com/music/search/mood/mysterious/',
    method: 'structured',
    source: 'pixabay',
    note: '',
    tracks,
  }
}

const load = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  load.mockClear()
  useLibraryStore.setState({ load })
})

afterEach(() => vi.restoreAllMocks())

const savedPage = new File(['<html></html>'], 'page.html', { type: 'text/html' })
const audio = (name: string) => new File(['x'], name, { type: 'audio/mpeg' })

describe('matchFiles', () => {
  it('matches by id first, then by the longest title', () => {
    const matched = matchFiles(
      [track(), riser, tensionRiser],
      [
        'leberch-investigation-604094.mp3',
        'my tension riser.mp3',
        'riser.mp3',
        'other.mp3',
      ]
    )
    expect([...matched.values()].map((t) => t?.title ?? null)).toEqual([
      'Investigation',
      'Tension Riser',
      'Riser',
      null,
    ])
  })
})

describe('SavedPageImport', () => {
  it('shows which track each file is before uploading, then imports them', async () => {
    vi.spyOn(importsApi, 'previewSavedPage').mockResolvedValue(preview([track(), riser]))
    const start = vi.spyOn(importsApi, 'importSavedPage').mockResolvedValue({
      page_url: 'https://pixabay.com/music/search/mood/mysterious/',
      source: 'pixabay',
      results: [
        {
          filename: 'leberch-investigation-604094.mp3',
          status: 'imported',
          asset_id: 'a1',
          track_title: 'Investigation',
          message: '',
        },
        {
          filename: 'other.mp3',
          status: 'unmatched',
          asset_id: 'a2',
          track_title: null,
          message: 'Imported, but not found on the page',
        },
      ],
    })
    const user = userEvent.setup()
    render(<SavedPageImport />)

    await user.upload(screen.getByLabelText('Saved page'), savedPage)
    expect(await screen.findByText(/2 tracks on the page/)).toBeInTheDocument()

    const files = [audio('leberch-investigation-604094.mp3'), audio('other.mp3')]
    await user.upload(screen.getByLabelText('Downloaded tracks'), files)
    expect(screen.getByText('→ Investigation · leberch')).toBeInTheDocument()
    expect(screen.getByText('Not on the page')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Import 2 files' }))

    expect(start).toHaveBeenCalledWith(savedPage, files)
    expect(await screen.findByText('Imported')).toBeInTheDocument()
    expect(
      screen.getByText(/other\.mp3 — Imported, but not found on the page/)
    ).toBeInTheDocument()
    expect(load).toHaveBeenCalled()
  })

  it('says when the page cannot be read', async () => {
    vi.spyOn(importsApi, 'previewSavedPage').mockRejectedValue(
      new Error('The saved page is too large to read')
    )
    const user = userEvent.setup()
    render(<SavedPageImport />)
    await user.upload(screen.getByLabelText('Saved page'), savedPage)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The saved page is too large to read'
    )
  })
})
