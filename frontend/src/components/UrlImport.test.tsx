import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'
import UrlImport from '@/components/UrlImport'
import { importsApi } from '@/api/imports'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { useActivityStore } from '@/stores/activity'

function queuedJob(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    kind: 'enrichment',
    action: 'import_url',
    status: 'queued',
    stalled: false,
    stage: 'Queued',
    progress: 0,
    detail: '',
    asset_id: null,
    asset_name: 'https://youtu.be/abc123',
    model: '',
    result_asset_id: null,
    result_asset_ids: [],
    error_message: null,
    created_at: '2026-09-28T10:00:00Z',
    updated_at: '2026-09-28T10:00:00Z',
    ...overrides,
  }
}

function codedError(code: string, message: string) {
  const error = new AxiosError('failed')
  // @ts-expect-error - a minimal response is all apiErrorMessage reads
  error.response = { data: { detail: { code, message } } }
  return error
}

afterEach(() => {
  useActivityStore.getState().reset()
  vi.restoreAllMocks()
})

describe('UrlImport', () => {
  it('cannot be submitted empty', () => {
    render(<UrlImport />)
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
  })

  it('queues the link with the default options', async () => {
    const user = userEvent.setup()
    const fromUrl = vi.spyOn(importsApi, 'fromUrl').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([queuedJob()])
    render(<UrlImport />)

    await user.type(screen.getByLabelText('Link to import'), '  youtu.be/abc123 ')
    await user.click(screen.getByRole('button', { name: 'Import' }))

    await waitFor(() => expect(fromUrl).toHaveBeenCalledTimes(1))
    // Tags applied and chapters clipped unless unticked — the user's defaults.
    expect(fromUrl.mock.calls[0][0]).toEqual({
      url: 'youtu.be/abc123',
      audio_only: false,
      apply_tags: true,
      chapters_as_clips: true,
    })
  })

  it('carries the options that were changed', async () => {
    const user = userEvent.setup()
    const fromUrl = vi.spyOn(importsApi, 'fromUrl').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    render(<UrlImport />)

    await user.click(screen.getByLabelText('Audio only'))
    await user.click(screen.getByLabelText(/apply the uploader.s tags/i))
    await user.click(screen.getByLabelText('Chapters as clips'))
    await user.type(screen.getByLabelText('Link to import'), 'https://youtu.be/x{Enter}')

    await waitFor(() => expect(fromUrl).toHaveBeenCalledTimes(1))
    expect(fromUrl.mock.calls[0][0]).toMatchObject({
      audio_only: true,
      apply_tags: false,
      chapters_as_clips: false,
    })
  })

  it('confirms, clears the box, and tells the activity feed', async () => {
    const user = userEvent.setup()
    vi.spyOn(importsApi, 'fromUrl').mockResolvedValue(queuedJob())
    const poll = vi.spyOn(activityApi, 'list').mockResolvedValue([queuedJob()])
    render(<UrlImport />)

    const input = screen.getByLabelText('Link to import')
    await user.type(input, 'https://youtu.be/abc123')
    await user.click(screen.getByRole('button', { name: 'Import' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Queued “https://youtu.be/abc123”'
    )
    expect(input).toHaveValue('')
    // Straight away, rather than on the idle poll ten seconds later: otherwise the
    // indicator says nothing is happening right after the user asked for something.
    await waitFor(() => expect(poll).toHaveBeenCalled())
  })

  it('keeps the options ticked for the next link', async () => {
    const user = userEvent.setup()
    vi.spyOn(importsApi, 'fromUrl').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    render(<UrlImport />)

    await user.click(screen.getByLabelText('Audio only'))
    await user.type(screen.getByLabelText('Link to import'), 'https://youtu.be/a{Enter}')
    await screen.findByRole('status')

    expect(screen.getByLabelText('Audio only')).toBeChecked()
  })

  it('shows why a link was refused and keeps it for correcting', async () => {
    const user = userEvent.setup()
    vi.spyOn(importsApi, 'fromUrl').mockRejectedValue(
      codedError('ssrf_blocked', 'The address must point to a public host')
    )
    render(<UrlImport />)

    const input = screen.getByLabelText('Link to import')
    await user.type(input, 'https://127.0.0.1/video{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The address must point to a public host'
    )
    expect(input).toHaveValue('https://127.0.0.1/video')
  })
})
