import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SelectionBar from '@/components/SelectionBar'
import { enrichmentApi } from '@/api/enrichment'
import { tagsApi } from '@/api/tags'
import { useActivityStore } from '@/stores/activity'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'
import type { Asset } from '@/api/assets'
import type { ActivityJob } from '@/api/transcripts'
import { noAttribution } from '@/test-fixtures'

function asset(id: string): Asset {
  return {
    id,
    name: id,
    description: null,
    summary: null,
    ...noAttribution,
    asset_type: 'video',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: null,
    mime_type: null,
    file_format: null,
    size_bytes: 0,
    duration_seconds: null,
    width: null,
    height: null,
    codec: null,
    file_url: null,
    thumb_url: null,
    missing: false,
    upload_date: '2026-09-15T00:00:00Z',
    modified_date: '2026-09-15T00:00:00Z',
    metadata_modified_date: '2026-09-15T00:00:00Z',
    tags: [],
  }
}

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    kind: 'enrichment',
    action: 'bulk_enrich',
    status: 'processing',
    stalled: false,
    stage: 'Summarising',
    progress: 40,
    detail: '3 of 8 · clip.mp4',
    asset_id: null,
    asset_name: '',
    model: '',
    result_asset_id: null,
    error_message: null,
    created_at: '2026-09-15T00:00:00Z',
    updated_at: '2026-09-15T00:00:00Z',
    ...overrides,
  }
}

function codedError(code: string) {
  const error = new AxiosError('failed')
  // @ts-expect-error - a minimal response is all apiErrorCode reads
  error.response = { data: { detail: { code, message: 'nope' } } }
  return error
}

function renderBar() {
  return render(
    <MemoryRouter>
      <SelectionBar
        selected={new Set(['a1', 'a2'])}
        assets={[asset('a1'), asset('a2')]}
        onSelectAll={() => {}}
        onClear={() => {}}
      />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useActivityStore.getState().reset()
  useLibraryStore.getState().reset()
  useTagStore.getState().reset()
  vi.spyOn(useActivityStore.getState(), 'refresh').mockResolvedValue(undefined)
  vi.spyOn(tagsApi, 'list').mockResolvedValue([])
})

afterEach(() => {
  useActivityStore.getState().reset()
  useLibraryStore.getState().reset()
  useTagStore.getState().reset()
  vi.restoreAllMocks()
})

describe('SelectionBar enrichment', () => {
  it('runs an action over exactly the selection', async () => {
    const bulk = vi.spyOn(enrichmentApi, 'bulk').mockResolvedValue(job())
    renderBar()

    await userEvent.click(screen.getByRole('button', { name: /summarise/i }))

    await waitFor(() => expect(bulk).toHaveBeenCalledWith('summarize', ['a1', 'a2']))
  })

  it('offers embed, which M5 deferred', async () => {
    const bulk = vi.spyOn(enrichmentApi, 'bulk').mockResolvedValue(job())
    renderBar()

    await userEvent.click(screen.getByRole('button', { name: /^embed$/i }))

    await waitFor(() => expect(bulk).toHaveBeenCalledWith('embed', ['a1', 'a2']))
  })

  it('does not offer transcription', () => {
    // Billed per minute of audio; a mis-click across a selection is expensive.
    renderBar()

    expect(screen.queryByRole('button', { name: /transcribe/i })).toBeNull()
  })

  it('shows one progress line for the whole run, not one per asset', () => {
    useActivityStore.setState({ jobs: [job()] })
    renderBar()

    expect(screen.getByText('3 of 8 · clip.mp4')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /summarise/i })).toBeDisabled()
  })

  it('points at Settings when nothing is configured', async () => {
    vi.spyOn(enrichmentApi, 'bulk').mockRejectedValue(codedError('provider_unavailable'))
    renderBar()

    await userEvent.click(screen.getByRole('button', { name: /describe/i }))

    expect(
      await screen.findByRole('link', { name: /add one in settings/i })
    ).toBeInTheDocument()
  })

  it('treats a missing embedder as the same kind of signpost', async () => {
    vi.spyOn(enrichmentApi, 'bulk').mockRejectedValue(codedError('embedding_unavailable'))
    renderBar()

    await userEvent.click(screen.getByRole('button', { name: /^embed$/i }))

    expect(
      await screen.findByRole('link', { name: /add one in settings/i })
    ).toBeInTheDocument()
  })

  it('shows a refusal as an error rather than a signpost', async () => {
    vi.spyOn(enrichmentApi, 'bulk').mockRejectedValue(codedError('selection_too_large'))
    renderBar()

    await userEvent.click(screen.getByRole('button', { name: /describe/i }))

    expect(await screen.findByText(/nope/i)).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('surfaces what a failed run said', () => {
    useActivityStore.setState({
      jobs: [
        job({
          status: 'error',
          error_message: 'Stopped after 3 assets failed in a row.',
        }),
      ],
    })
    renderBar()

    expect(screen.getByText(/3 assets failed in a row/i)).toBeInTheDocument()
  })

  it('ignores another kind of job', () => {
    useActivityStore.setState({ jobs: [job({ action: 'backfill_embeddings' })] })
    renderBar()

    expect(screen.getByRole('button', { name: /summarise/i })).toBeEnabled()
  })
})
