import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VersionsTab from '@/components/VersionsTab'
import { versionsApi, type AssetVersion } from '@/api/versions'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import type { Asset } from '@/api/assets'
import { useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'
import { noAttribution } from '@/test-fixtures'

const MB = 1024 * 1024

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
    size_bytes: 900 * MB,
    duration_seconds: 60,
    width: 1920,
    height: 1080,
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

const v720: AssetVersion = {
  id: 'v720',
  asset_id: 'a1',
  short_side: 720,
  width: 1280,
  height: 720,
  size_bytes: 200 * MB,
  codec: 'h264',
  mime_type: 'video/mp4',
  file_url: '/media/u/v720.mp4?exp=1&sig=x',
  created_at: '2026-01-01T00:00:00',
}

function renderTab(asset: Asset = makeVideo()) {
  return render(
    <MemoryRouter>
      <VersionsTab asset={asset} />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useActivityStore.setState({ jobs: [] })
  useLibraryStore.getState().reset()
  vi.spyOn(activityApi, 'list').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('VersionsTab', () => {
  it('lists the original and each smaller size, made or not', async () => {
    vi.spyOn(versionsApi, 'list').mockResolvedValue([v720])
    renderTab()

    expect(await screen.findByText('Original')).toBeInTheDocument()
    expect(screen.getByText('1920 × 1080 · 900.0 MB')).toBeInTheDocument()
    // 1080p is the original's own size, so not on offer; 720p exists.
    expect(screen.queryByText('1080p')).not.toBeInTheDocument()
    expect(screen.getByText('1280 × 720 · 200.0 MB')).toBeInTheDocument()
    // 480p and 360p are not made yet.
    expect(screen.getAllByRole('button', { name: 'Create' })).toHaveLength(2)
    expect(screen.getByText('1 version · 200.0 MB')).toBeInTheDocument()
  })

  it('starts the size whose Create was pressed', async () => {
    vi.spyOn(versionsApi, 'list').mockResolvedValue([])
    const create = vi
      .spyOn(versionsApi, 'create')
      .mockResolvedValue({ id: 'j1' } as ActivityJob)
    renderTab()

    const buttons = await screen.findAllByRole('button', { name: 'Create' })
    // 720p, 480p, 360p, in that order.
    await userEvent.click(buttons[1])

    await waitFor(() => expect(create).toHaveBeenCalledWith('a1', 480))
  })

  it('asks before making a version the original', async () => {
    vi.spyOn(versionsApi, 'list').mockResolvedValue([v720])
    const makeOriginal = vi
      .spyOn(versionsApi, 'makeOriginal')
      .mockResolvedValue(makeVideo({ width: 1280, height: 720 }))
    vi.spyOn(useLibraryStore.getState(), 'refreshAsset').mockResolvedValue()
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Make original' }))
    expect(makeOriginal).not.toHaveBeenCalled()
    expect(screen.getByText(/cannot be brought back/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Replace original' }))
    await waitFor(() => expect(makeOriginal).toHaveBeenCalledWith('a1', 'v720'))
  })

  it('asks before deleting a version', async () => {
    vi.spyOn(versionsApi, 'list').mockResolvedValue([v720])
    const remove = vi.spyOn(versionsApi, 'remove').mockResolvedValue()
    renderTab()

    await userEvent.click(
      await screen.findByRole('button', { name: 'Delete the 720p version' })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Delete version' }))

    await waitFor(() => expect(remove).toHaveBeenCalledWith('a1', 'v720'))
    await waitFor(() =>
      expect(screen.queryByText('1280 × 720 · 200.0 MB')).not.toBeInTheDocument()
    )
  })

  it('offers nothing new while a version is being made', async () => {
    vi.spyOn(versionsApi, 'list').mockResolvedValue([])
    useActivityStore.setState({
      jobs: [
        {
          id: 'j1',
          action: 'make_version',
          asset_id: 'a1',
          status: 'processing',
          stage: 'Encoding',
          progress: 40,
        } as ActivityJob,
      ],
    })
    renderTab()

    const buttons = await screen.findAllByRole('button', { name: 'Create' })
    expect(buttons.every((b) => (b as HTMLButtonElement).disabled)).toBe(true)
  })
})
