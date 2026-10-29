import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReencodePanel from '@/components/ReencodePanel'
import { clipsApi } from '@/api/clips'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import type { Asset } from '@/api/assets'
import { useLibraryStore } from '@/stores/library'
import { noAttribution } from '@/test-fixtures'

function makeVideo(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Harbour',
    description: null,
    summary: null,
    asset_type: 'video',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: null,
    mime_type: null,
    file_format: null,
    size_bytes: 0,
    duration_seconds: 60,
    width: 3840,
    height: 2160,
    codec: null,
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

function renderPanel(asset: Asset) {
  return render(
    <MemoryRouter>
      <ReencodePanel asset={asset} />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useLibraryStore.getState().reset()
  vi.spyOn(activityApi, 'list').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ReencodePanel', () => {
  it('offers only sizes smaller than the video, defaulting to 1080p', () => {
    renderPanel(makeVideo())
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toEqual(['1440p', '1080p', '720p', '480p', '360p'])
    expect(screen.getByRole('combobox')).toHaveValue('1080')
  })

  it('reads a portrait video by its short side', () => {
    renderPanel(makeVideo({ width: 1080, height: 1920 }))
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toEqual(['720p', '480p', '360p'])
  })

  it('renders nothing for a video already at the smallest size', () => {
    const { container } = renderPanel(makeVideo({ width: 640, height: 360 }))
    expect(container).toBeEmptyDOMElement()
  })

  it('sends the chosen size', async () => {
    const reencode = vi
      .spyOn(clipsApi, 'reencode')
      .mockResolvedValue({ id: 'j1' } as ActivityJob)
    renderPanel(makeVideo())

    await userEvent.selectOptions(screen.getByRole('combobox'), '720')
    await userEvent.click(screen.getByRole('button', { name: 'Re-encode to 720p' }))

    await waitFor(() => expect(reencode).toHaveBeenCalledWith('a1', 720))
  })
})
