import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AttributionPanel from '@/components/AttributionPanel'
import { useLibraryStore } from '@/stores/library'
import type { Asset } from '@/api/assets'
import { noAttribution } from '@/test-fixtures'

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Interview',
    description: null,
    summary: null,
    ...noAttribution,
    asset_type: 'video',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: 'interview.mp4',
    mime_type: 'video/mp4',
    file_format: 'mp4',
    size_bytes: 100,
    duration_seconds: 60,
    width: 1920,
    height: 1080,
    codec: 'h264',
    file_url: null,
    thumb_url: null,
    missing: false,
    tags: [],
    upload_date: '2026-01-01T00:00:00',
    modified_date: '2026-01-01T00:00:00',
    metadata_modified_date: '2026-01-01T00:00:00',
    ...overrides,
  }
}

const update = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  update.mockClear()
  useLibraryStore.setState({ update })
})

describe('AttributionPanel', () => {
  it('shows the fields that are filled in', () => {
    render(<AttributionPanel asset={asset({ creator: 'Jane Doe', publisher: 'BBC' })} />)

    expect(screen.getByLabelText('Creator')).toHaveValue('Jane Doe')
    expect(screen.getByLabelText('Publisher')).toHaveValue('BBC')
  })

  it('previews the composed credit as you type', async () => {
    const user = userEvent.setup()
    render(<AttributionPanel asset={asset()} />)

    await user.type(screen.getByLabelText('Creator'), 'Jane Doe')
    await user.type(screen.getByLabelText('Publisher'), 'BBC')

    expect(screen.getByText('Jane Doe — BBC')).toBeInTheDocument()
  })

  it('prefers a typed credit line over the composition', async () => {
    const user = userEvent.setup()
    render(<AttributionPanel asset={asset({ publisher: 'BBC' })} />)

    await user.type(screen.getByLabelText('Credit line'), 'Courtesy of the BBC')

    expect(screen.getByText('Courtesy of the BBC')).toBeInTheDocument()
    expect(screen.queryByText('BBC', { selector: 'p' })).not.toBeInTheDocument()
  })

  it('sends only the fields that changed', async () => {
    const user = userEvent.setup()
    render(<AttributionPanel asset={asset({ creator: 'Jane Doe' })} />)

    await user.type(screen.getByLabelText('Publisher'), 'BBC')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    // Not `creator` as well: the API stamps every key it receives as human-written, so
    // resending an untouched field would mark it hand-verified when it was not.
    await waitFor(() => expect(update).toHaveBeenCalledWith('a1', { publisher: 'BBC' }))
  })

  it('clears a field by sending null rather than an empty string', async () => {
    const user = userEvent.setup()
    render(<AttributionPanel asset={asset({ publisher: 'BBC' })} />)

    await user.clear(screen.getByLabelText('Publisher'))
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(update).toHaveBeenCalledWith('a1', { publisher: null }))
  })

  it('cannot be saved until something changes', () => {
    render(<AttributionPanel asset={asset({ publisher: 'BBC' })} />)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('refuses a malformed published date before it reaches the server', async () => {
    const user = userEvent.setup()
    render(<AttributionPanel asset={asset()} />)

    await user.type(screen.getByLabelText('Published'), 'summer 1994')

    expect(screen.getByText('Use YYYY, YYYY-MM or YYYY-MM-DD.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(update).not.toHaveBeenCalled()
  })

  it('accepts a bare year', async () => {
    const user = userEvent.setup()
    render(<AttributionPanel asset={asset()} />)

    await user.type(screen.getByLabelText('Published'), '1994')

    expect(screen.queryByText('Use YYYY, YYYY-MM or YYYY-MM-DD.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('marks an inherited value as inherited', () => {
    render(
      <AttributionPanel
        asset={asset({
          publisher: 'BBC',
          parent_asset_id: 'parent-1',
          attribution_inherited: ['publisher'],
        })}
      />
    )

    expect(screen.getByText('inherited from the original')).toBeInTheDocument()
  })

  it('does not mark a value the asset carries itself', () => {
    render(<AttributionPanel asset={asset({ publisher: 'BBC' })} />)
    expect(screen.queryByText(/inherited from/)).not.toBeInTheDocument()
  })

  it('copies the credit', async () => {
    // Spied rather than replaced: userEvent.setup() installs its own clipboard stub and
    // defines it non-configurably, so assigning over it silently does nothing and the
    // spy never sees the call.
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()

    render(
      <AttributionPanel asset={asset({ credit: 'Jane Doe — BBC', publisher: 'BBC' })} />
    )

    await user.click(screen.getByRole('button', { name: 'Copy this credit' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Jane Doe — BBC'))
  })

  it('keeps the draft when a save is refused', async () => {
    update.mockRejectedValueOnce(new Error('nope'))
    const user = userEvent.setup()
    render(<AttributionPanel asset={asset()} />)

    await user.type(screen.getByLabelText('Publisher'), 'BBC')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText(/Could not save/)).toBeInTheDocument()
    expect(screen.getByLabelText('Publisher')).toHaveValue('BBC')
  })

  it('does not let Escape reach the panel behind it', async () => {
    const onKeyDown = vi.fn()
    const user = userEvent.setup()
    render(
      <div onKeyDown={onKeyDown}>
        <AttributionPanel asset={asset()} />
      </div>
    )

    await user.click(screen.getByLabelText('Creator'))
    await user.keyboard('{Escape}')

    // DetailDock listens for Escape to close the whole asset panel; abandoning a
    // half-typed credit must not also shut the asset.
    expect(onKeyDown).not.toHaveBeenCalled()
  })
})
