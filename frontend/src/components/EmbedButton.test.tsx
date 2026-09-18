import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { AxiosError } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'
import EmbedButton from '@/components/EmbedButton'
import { embeddingsApi } from '@/api/embeddings'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { useActivityStore } from '@/stores/activity'

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'j1',
    kind: 'enrichment',
    action: 'embed',
    status: 'processing',
    stalled: false,
    stage: 'Embedding metadata',
    progress: 10,
    detail: '',
    asset_id: 'a1',
    asset_name: 'Interview',
    model: 'text-embedding-3-small',
    result_asset_id: null,
    error_message: null,
    created_at: '2026-09-14T10:00:00Z',
    updated_at: '2026-09-14T10:00:00Z',
    ...overrides,
  }
}

function unavailable(): AxiosError {
  const error = new AxiosError('no provider')
  error.response = {
    data: { detail: { code: 'embedding_unavailable', message: 'No embedding provider' } },
    status: 400,
    statusText: '',
    headers: {},
    config: {} as never,
  }
  return error
}

function renderButton() {
  return render(
    <MemoryRouter>
      <EmbedButton assetId="a1" />
    </MemoryRouter>
  )
}

afterEach(() => {
  useActivityStore.getState().reset()
  vi.restoreAllMocks()
})

describe('EmbedButton', () => {
  it('queues an embedding for this asset', async () => {
    const embed = vi.spyOn(embeddingsApi, 'embedAsset').mockResolvedValue(job())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    renderButton()

    await userEvent.click(
      screen.getByRole('button', { name: /embed for semantic search/i })
    )

    expect(embed).toHaveBeenCalledWith('a1')
  })

  it('shows progress from the store rather than opening its own poll', async () => {
    useActivityStore.setState({ jobs: [job()] })
    renderButton()

    expect(screen.getByRole('button', { name: /embedding metadata/i })).toBeDisabled()
  })

  it('ignores a job belonging to a different asset', () => {
    useActivityStore.setState({ jobs: [job({ asset_id: 'somebody-else' })] })
    renderButton()

    expect(
      screen.getByRole('button', { name: /embed for semantic search/i })
    ).toBeEnabled()
  })

  it('points at Settings when no provider is configured', async () => {
    // That error is a signpost, not a failure — the place to fix it is one click away.
    vi.spyOn(embeddingsApi, 'embedAsset').mockRejectedValue(unavailable())
    renderButton()

    await userEvent.click(
      screen.getByRole('button', { name: /embed for semantic search/i })
    )

    expect(
      await screen.findByRole('link', { name: /add one in settings/i })
    ).toHaveAttribute('href', '/settings')
  })
})
