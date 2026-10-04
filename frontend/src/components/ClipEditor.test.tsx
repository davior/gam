import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ClipEditor from '@/components/ClipEditor'
import { clipsApi } from '@/api/clips'
import { activityApi } from '@/api/transcripts'
import type { Asset } from '@/api/assets'
import { noAttribution } from '@/test-fixtures'

function makeAsset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Interview',
    description: null,
    summary: null,
    asset_type: 'video',
    source: 'upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: null,
    mime_type: null,
    file_format: null,
    size_bytes: 0,
    duration_seconds: 60,
    width: null,
    height: null,
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

const liveClip = makeAsset({
  id: 'c1',
  name: 'Live clip',
  source: 'clip',
  parent_asset_id: 'a1',
  in_point: 5,
  out_point: 9,
})
const subVideo = makeAsset({
  id: 'c2',
  name: 'Standalone cut',
  source: 'sub_video',
  parent_asset_id: 'a1',
  in_point: 10,
  out_point: 20,
})

beforeEach(() => {
  vi.spyOn(activityApi, 'list').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

function renderEditor() {
  render(
    <MemoryRouter>
      <ClipEditor asset={makeAsset()} currentTime={0} onSeek={vi.fn()} />
    </MemoryRouter>
  )
}

describe('ClipEditor', () => {
  it('offers "Extract as file" on a live clip but not on an extracted one', async () => {
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip, subVideo])

    renderEditor()

    // Exactly one: the sub-video already owns a file.
    expect(
      await screen.findAllByRole('button', { name: 'Extract as file' })
    ).toHaveLength(1)
  })

  it("promotes the clip in place using the clip's own id", async () => {
    vi.spyOn(clipsApi, 'list').mockResolvedValue([liveClip])
    const promote = vi.spyOn(clipsApi, 'promote').mockResolvedValue({
      id: 'j1',
      asset_id: 'c1',
      action: 'extract_subvideo',
      status: 'queued',
    } as never)

    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: 'Extract as file' }))

    expect(promote).toHaveBeenCalledWith('c1')
  })
})
