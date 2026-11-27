import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DocumentTextPanel from '@/components/DocumentTextPanel'
import { documentsApi } from '@/api/documents'
import type { Asset } from '@/api/assets'
import { enrichmentApi, type DocumentPage } from '@/api/enrichment'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { useActivityStore } from '@/stores/activity'

function page(overrides: Partial<DocumentPage> = {}): DocumentPage {
  return {
    id: 'p1',
    idx: 0,
    page_number: 1,
    label: 'Page 1',
    text: 'Gecko Asset Manager',
    ...overrides,
  }
}

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    asset_id: 'a1',
    asset_name: 'report.pdf',
    action: 'extract_text',
    status: 'processing',
    stage: 'Extracting text',
    progress: 40,
    detail: '',
    error_message: null,
    created_at: '2026-09-16T10:00:00Z',
    updated_at: '2026-09-16T10:00:00Z',
    ...overrides,
  } as ActivityJob
}

afterEach(() => {
  // Unmount before touching the store or the spies: the panel reloads whenever its job
  // changes, so emptying the store under a mounted panel — with the spies already
  // restored — sends a real request to nowhere.
  cleanup()
  useActivityStore.setState({ jobs: [] })
  vi.restoreAllMocks()
})

describe('DocumentTextPanel', () => {
  it('offers the Extract text button', async () => {
    vi.spyOn(enrichmentApi, 'text').mockResolvedValue([])
    vi.spyOn(activityApi, 'list').mockResolvedValue([])

    render(<DocumentTextPanel assetId="a1" />)

    expect(await screen.findByRole('button', { name: /extract text/i })).toBeEnabled()
  })

  it('shows the extracted text with its labels', async () => {
    vi.spyOn(enrichmentApi, 'text').mockResolvedValue([
      page(),
      page({ id: 'p2', idx: 1, page_number: 2, label: 'Page 2', text: 'Second page.' }),
    ])
    vi.spyOn(activityApi, 'list').mockResolvedValue([])

    render(<DocumentTextPanel assetId="a1" />)

    expect(await screen.findByText('Gecko Asset Manager')).toBeInTheDocument()
    expect(screen.getByText('Page 2')).toBeInTheDocument()
    expect(screen.getByText('2 sections')).toBeInTheDocument()
  })

  it('says how to make the document summarisable when nothing has been read', async () => {
    vi.spyOn(enrichmentApi, 'text').mockResolvedValue([])
    vi.spyOn(activityApi, 'list').mockResolvedValue([])

    render(<DocumentTextPanel assetId="a1" />)

    expect(await screen.findByText(/run extract text/i)).toBeInTheDocument()
  })

  it('shows only this asset’s extraction, not whatever else is running on it', async () => {
    // `activityApi.list` returns every active job for the asset. Taking jobs[0] would
    // put a summarise job's progress under this heading — the mistake TranscriptPanel
    // already records.
    vi.spyOn(enrichmentApi, 'text').mockResolvedValue([])
    vi.spyOn(activityApi, 'list').mockResolvedValue([
      job({ id: 'j2', action: 'summarize', stage: 'Summarising' }),
      job(),
    ])

    render(<DocumentTextPanel assetId="a1" />)

    expect(await screen.findByText('Extracting text')).toBeInTheDocument()
    expect(screen.queryByText('Summarising')).not.toBeInTheDocument()
  })

  it('shows the text when the extraction was started from another tab', async () => {
    // Tabs keeps every panel mounted, so this one has already loaded — empty, with
    // nothing running — by the time "Extract text" is pressed on the Info tab. The
    // button knows nothing of this panel; the activity store is the only thing both can
    // hear, and without listening to it the panel says "no text yet" for good.
    const text = vi.spyOn(enrichmentApi, 'text').mockResolvedValue([])
    vi.spyOn(activityApi, 'list').mockResolvedValue([])

    render(<DocumentTextPanel assetId="a1" />)
    expect(await screen.findByText(/run extract text/i)).toBeInTheDocument()

    text.mockResolvedValue([page()])
    act(() => useActivityStore.setState({ jobs: [job({ status: 'done' })] }))

    expect(await screen.findByText('Gecko Asset Manager')).toBeInTheDocument()
  })

  it('says why when the extraction failed, instead of asking for another run', async () => {
    // A scan has no text to read, and the reason lives on the failed job. The empty
    // state invites a re-run, so the reason has to be on screen beside it.
    vi.spyOn(enrichmentApi, 'text').mockResolvedValue([])
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    useActivityStore.setState({
      jobs: [
        job({
          status: 'error',
          error_message: 'No text could be read from this document.',
        }),
      ],
    })

    render(<DocumentTextPanel assetId="a1" />)

    expect(await screen.findByText(/no text could be read/i)).toBeInTheDocument()
  })

  describe('grabbing a page as an image', () => {
    function pdfPanel() {
      return render(
        <MemoryRouter>
          <DocumentTextPanel assetId="a1" isPdf />
        </MemoryRouter>
      )
    }

    it('offers nothing for a document that is not a PDF', async () => {
      vi.spyOn(enrichmentApi, 'text').mockResolvedValue([page()])
      vi.spyOn(activityApi, 'list').mockResolvedValue([])
      const count = vi.spyOn(documentsApi, 'pageCount')

      render(<DocumentTextPanel assetId="a1" />)

      await screen.findByText('Gecko Asset Manager')
      expect(screen.queryByText(/grab a page as an image/i)).not.toBeInTheDocument()
      expect(
        screen.queryByRole('button', { name: /grab as image/i })
      ).not.toBeInTheDocument()
      expect(count).not.toHaveBeenCalled()
    })

    it('grabs the page typed in, even when the PDF has no text to list', async () => {
      vi.spyOn(enrichmentApi, 'text').mockResolvedValue([])
      vi.spyOn(activityApi, 'list').mockResolvedValue([])
      vi.spyOn(documentsApi, 'pageCount').mockResolvedValue(4)
      const grab = vi
        .spyOn(documentsApi, 'grabPage')
        .mockResolvedValue({ id: 'img1', name: 'report p.3' } as Asset)

      pdfPanel()
      expect(await screen.findByText('of 4')).toBeInTheDocument()
      const input = screen.getByLabelText('Page')
      await userEvent.clear(input)
      await userEvent.type(input, '3')
      await userEvent.click(screen.getByRole('button', { name: 'Grab page' }))

      expect(grab).toHaveBeenCalledWith('a1', { page: 3 })
      expect(await screen.findByText(/Saved/)).toHaveTextContent('report p.3')
      expect(screen.getByRole('link', { name: 'view it' })).toHaveAttribute(
        'href',
        '/a/img1'
      )
    })

    it('will not grab a page past the end', async () => {
      vi.spyOn(enrichmentApi, 'text').mockResolvedValue([])
      vi.spyOn(activityApi, 'list').mockResolvedValue([])
      vi.spyOn(documentsApi, 'pageCount').mockResolvedValue(2)

      pdfPanel()
      await screen.findByText('of 2')
      const input = screen.getByLabelText('Page')
      await userEvent.clear(input)
      await userEvent.type(input, '5')

      expect(screen.getByRole('button', { name: 'Grab page' })).toBeDisabled()
    })

    it('grabs a listed page from its own button', async () => {
      vi.spyOn(enrichmentApi, 'text').mockResolvedValue([
        page(),
        page({ id: 'p2', idx: 1, page_number: 2, label: 'Page 2', text: 'Second.' }),
      ])
      vi.spyOn(activityApi, 'list').mockResolvedValue([])
      vi.spyOn(documentsApi, 'pageCount').mockResolvedValue(2)
      const grab = vi
        .spyOn(documentsApi, 'grabPage')
        .mockResolvedValue({ id: 'img2', name: 'report p.2' } as Asset)

      pdfPanel()
      const buttons = await screen.findAllByRole('button', { name: /grab as image/i })
      await userEvent.click(buttons[1])

      expect(grab).toHaveBeenCalledWith('a1', { page: 2 })
    })

    it('says why a grab failed', async () => {
      vi.spyOn(enrichmentApi, 'text').mockResolvedValue([])
      vi.spyOn(activityApi, 'list').mockResolvedValue([])
      vi.spyOn(documentsApi, 'pageCount').mockResolvedValue(3)
      vi.spyOn(documentsApi, 'grabPage').mockRejectedValue(
        new Error('This PDF has 3 pages, so there is no page 9.')
      )

      pdfPanel()
      await screen.findByText('of 3')
      await userEvent.click(screen.getByRole('button', { name: 'Grab page' }))

      expect(await screen.findByText(/there is no page 9/i)).toBeInTheDocument()
    })
  })
})
