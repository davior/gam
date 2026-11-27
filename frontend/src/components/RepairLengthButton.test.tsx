import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RepairLengthButton from '@/components/RepairLengthButton'
import { needsLengthRepair, versionsApi } from '@/api/versions'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import type { Asset } from '@/api/assets'
import { useActivityStore } from '@/stores/activity'
import { noAttribution } from '@/test-fixtures'

function makeAsset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: '2026, 10:26:13 am.webm',
    description: null,
    summary: null,
    asset_type: 'video',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: null,
    mime_type: 'video/webm',
    file_format: 'webm',
    size_bytes: 555_800_000,
    duration_seconds: null,
    width: 1280,
    height: 720,
    codec: 'vp8',
    file_url: null,
    thumb_url: null,
    missing: false,
    tags: [],
    ...noAttribution,
    upload_date: '2026-01-01T00:00:00',
    modified_date: '2026-01-01T00:00:00',
    metadata_modified_date: '2026-01-01T00:00:00',
    ...overrides,
  }
}

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    kind: 'enrichment',
    action: 'remux',
    status: 'queued',
    stalled: false,
    stage: 'Queued',
    progress: 0,
    detail: '',
    asset_id: 'a1',
    asset_name: 'rec.webm',
    model: '',
    result_asset_id: null,
    result_asset_ids: [],
    error_message: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('needsLengthRepair', () => {
  it('is for a video or sound with a file and no stored length', () => {
    expect(needsLengthRepair(makeAsset())).toBe(true)
    expect(needsLengthRepair(makeAsset({ asset_type: 'audio' }))).toBe(true)
    // 0 is as good as none: a player cannot do anything with it.
    expect(needsLengthRepair(makeAsset({ duration_seconds: 0 }))).toBe(true)
  })

  it('goes away once the file has a length', () => {
    expect(needsLengthRepair(makeAsset({ duration_seconds: 1800 }))).toBe(false)
  })

  it('is not for a picture, a document, a clip or a missing file', () => {
    expect(needsLengthRepair(makeAsset({ asset_type: 'image' }))).toBe(false)
    expect(needsLengthRepair(makeAsset({ asset_type: 'document' }))).toBe(false)
    // A clip owns no file of its own: it is a window onto its parent's.
    expect(needsLengthRepair(makeAsset({ source: 'clip' }))).toBe(false)
    expect(needsLengthRepair(makeAsset({ missing: true }))).toBe(false)
  })
})

describe('RepairLengthButton', () => {
  beforeEach(() => {
    useActivityStore.setState({ jobs: [] })
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
  })
  afterEach(() => vi.restoreAllMocks())

  const renderIt = (asset: Asset) =>
    render(
      <MemoryRouter>
        <RepairLengthButton asset={asset} />
      </MemoryRouter>
    )

  it('renders nothing for a file that already has a length', () => {
    const { container } = renderIt(makeAsset({ duration_seconds: 1800 }))
    expect(container).toBeEmptyDOMElement()
  })

  it('explains the problem and starts the repair when pressed', async () => {
    const remux = vi.spyOn(versionsApi, 'remux').mockResolvedValue(job())
    renderIt(makeAsset())

    expect(screen.getByText(/no length stored/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Repair length' }))

    await waitFor(() => expect(remux).toHaveBeenCalledWith('a1'))
  })

  it('shows the server’s sentence when it refuses', async () => {
    vi.spyOn(versionsApi, 'remux').mockRejectedValue(
      new Error('A version of this video is already being made')
    )
    renderIt(makeAsset())

    await userEvent.click(screen.getByRole('button', { name: 'Repair length' }))

    expect(await screen.findByText(/already being made/i)).toBeInTheDocument()
  })
})
