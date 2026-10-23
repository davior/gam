import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DocumentTextPanel from '@/components/DocumentTextPanel'
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
    // A scan has no text to read, and the reason lives on the failed job. Shown only on
    // the Info tab it is invisible from here, where the empty state invites a re-run
    // that cannot work.
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
})
