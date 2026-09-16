import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DocumentTextPanel from '@/components/DocumentTextPanel'
import { enrichmentApi, type DocumentPage } from '@/api/enrichment'
import { activityApi, type ActivityJob } from '@/api/transcripts'

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
})
