import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import AssetThumb from '@/components/AssetThumb'
import type { Asset } from '@/api/assets'
import { noAttribution } from '@/test-fixtures'

function makeAsset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Test',
    description: null,
    summary: null,
    ...noAttribution,
    asset_type: 'image',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: 'test.jpg',
    mime_type: 'image/jpeg',
    file_format: 'jpg',
    size_bytes: 100,
    duration_seconds: null,
    width: null,
    height: null,
    codec: null,
    file_url: '/media/u/a1.jpg?exp=1&sig=x',
    thumb_url: '/media/u/a1.thumb.jpg?exp=1&sig=x',
    missing: false,
    tags: [],
    upload_date: '2026-09-13T10:00:00Z',
    modified_date: '2026-09-13T10:00:00Z',
    metadata_modified_date: '2026-09-13T10:00:00Z',
    ...overrides,
  }
}

describe('AssetThumb', () => {
  it('shows the thumbnail when there is one', () => {
    render(<AssetThumb asset={makeAsset()} />)
    expect(screen.getByRole('presentation', { hidden: true })).toBeTruthy()
  })

  it('warns when the file is missing rather than showing a broken image', () => {
    // A missing file and a format with no preview mean different things to the user;
    // collapsing them would present missing data as normal.
    const { container } = render(<AssetThumb asset={makeAsset({ missing: true })} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[title*="missing"]')).not.toBeNull()
  })

  it('falls back to a type icon when there is no preview', () => {
    const { container } = render(
      <AssetThumb asset={makeAsset({ asset_type: 'audio', thumb_url: null })} />
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).not.toBeNull()
  })
})
