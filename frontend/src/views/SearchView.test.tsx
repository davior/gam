import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SearchView from '@/views/SearchView'
import { searchApi, type SearchHit, type SearchResponse } from '@/api/search'
import type { Asset } from '@/api/assets'

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Giordano interview',
    description: null,
    summary: null,
    asset_type: 'video',
    source: 'local_upload',
    original_name: 'giordano.mp4',
    mime_type: 'video/mp4',
    file_format: 'mp4',
    size_bytes: 100,
    duration_seconds: 3600,
    width: 1920,
    height: 1080,
    codec: 'h264',
    file_url: '/media/u/a1.mp4?exp=1&sig=x',
    thumb_url: '/media/u/a1.thumb.jpg?exp=1&sig=x',
    missing: false,
    tags: [],
    upload_date: '2026-09-13T10:00:00Z',
    modified_date: '2026-09-13T10:00:00Z',
    metadata_modified_date: '2026-09-13T10:00:00Z',
    ...overrides,
  }
}

function hit(overrides: Partial<SearchHit> = {}): SearchHit {
  return {
    asset: asset(),
    score: 0.03,
    snippet: 'We are deploying «nano» «weapons» via aerosol dispersion.',
    start_time: 412,
    segment_id: 's1',
    sources: ['keyword', 'semantic'],
    other_matches: 0,
    ...overrides,
  }
}

function response(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    data: [hit()],
    total: 1,
    query: 'nano weapons',
    semantic: true,
    semantic_error: null,
    ...overrides,
  }
}

function renderView() {
  return render(
    <MemoryRouter>
      <SearchView />
    </MemoryRouter>
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SearchView', () => {
  it('shows the matching moment and its timestamp', async () => {
    vi.spyOn(searchApi, 'run').mockResolvedValue(response())
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')

    // 412 seconds is 6:52 — the second to jump to, which is the point of the feature.
    expect(await screen.findByText('6:52')).toBeInTheDocument()
    expect(screen.getByText('Giordano interview')).toBeInTheDocument()
  })

  it('highlights the matched words', async () => {
    vi.spyOn(searchApi, 'run').mockResolvedValue(response())
    const { container } = renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')
    await screen.findByText('6:52')

    const marks = Array.from(container.querySelectorAll('mark')).map((m) => m.textContent)
    expect(marks).toEqual(['nano', 'weapons'])
  })

  it('says when meaning-based search is unavailable rather than silently degrading', async () => {
    // "No results" means something different when only half the search ran.
    vi.spyOn(searchApi, 'run').mockResolvedValue(
      response({ data: [], total: 0, semantic: false })
    )
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')

    expect(await screen.findByText(/nothing matched/i)).toBeInTheDocument()
    expect(screen.getByText(/only exact words are searched/i)).toBeInTheDocument()
  })

  it('reports a failing embedding provider', async () => {
    vi.spyOn(searchApi, 'run').mockResolvedValue(
      response({ semantic: false, semantic_error: 'OpenAI rejected the API key' })
    )
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')

    expect(await screen.findByText(/OpenAI rejected the API key/)).toBeInTheDocument()
  })

  it('counts other moments rather than hiding them', async () => {
    vi.spyOn(searchApi, 'run').mockResolvedValue(
      response({ data: [hit({ other_matches: 4 })] })
    )
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')

    expect(
      await screen.findByText(/and 4 more moments in this asset/i)
    ).toBeInTheDocument()
  })

  it('marks a hit that only semantic search found', async () => {
    vi.spyOn(searchApi, 'run').mockResolvedValue(
      response({
        data: [hit({ sources: ['semantic'], snippet: 'no shared words at all' })],
      })
    )
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')

    expect(await screen.findByTitle(/found by meaning/i)).toBeInTheDocument()
  })

  it('does not search for an empty query', async () => {
    const run = vi.spyOn(searchApi, 'run').mockResolvedValue(response())
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), '   ')

    await waitFor(() => {
      expect(run).not.toHaveBeenCalled()
    })
  })

  it('debounces rather than firing per keystroke', async () => {
    const run = vi.spyOn(searchApi, 'run').mockResolvedValue(response())
    renderView()

    await userEvent.type(
      screen.getByPlaceholderText(/what are you looking for/i),
      'nano weapons'
    )

    await waitFor(() => expect(run).toHaveBeenCalled())
    // Twelve characters typed; nowhere near twelve requests.
    expect(run.mock.calls.length).toBeLessThan(4)
  })
})

describe('SearchView filters and paging', () => {
  /** The params the last search was actually run with — the gap these tests exist for
   *  is that `searchApi.run` typed both of these and the view passed neither. */
  function lastParams(spy: { mock: { calls: unknown[][] } }) {
    const calls = spy.mock.calls
    return calls[calls.length - 1]?.[1]
  }

  it('sends the server default limit with an unfiltered search', async () => {
    const spy = vi.spyOn(searchApi, 'run').mockResolvedValue(response())
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')
    await screen.findByText('6:52')

    expect(lastParams(spy)).toMatchObject({ limit: 30 })
    expect(lastParams(spy)).not.toHaveProperty('asset_type')
  })

  it('sends the chosen asset type', async () => {
    const spy = vi.spyOn(searchApi, 'run').mockResolvedValue(response())
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')
    await screen.findByText('6:52')
    await userEvent.click(screen.getByRole('button', { name: 'Documents' }))

    await waitFor(() => expect(lastParams(spy)).toMatchObject({ asset_type: 'document' }))
  })

  it('asks for a bigger page rather than an offset, because there is no offset', async () => {
    const spy = vi
      .spyOn(searchApi, 'run')
      .mockResolvedValue(response({ data: Array.from({ length: 30 }, () => hit()) }))
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')
    await screen.findByRole('button', { name: /show more/i })
    await userEvent.click(screen.getByRole('button', { name: /show more/i }))

    await waitFor(() => expect(lastParams(spy)).toMatchObject({ limit: 60 }))
  })

  it('offers no "show more" when the page is not full', async () => {
    vi.spyOn(searchApi, 'run').mockResolvedValue(response())
    renderView()

    await userEvent.type(screen.getByPlaceholderText(/what are you looking for/i), 'nano')
    await screen.findByText('6:52')

    expect(screen.queryByRole('button', { name: /show more/i })).not.toBeInTheDocument()
  })
})
