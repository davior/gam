import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsView from '@/views/SettingsView'
import { embeddingsApi, type EmbeddingCoverage } from '@/api/embeddings'
import { useActivityStore } from '@/stores/activity'
import {
  embeddingSettingsApi,
  speechSettingsApi,
  type EmbeddingSettings,
  type SpeechSettings,
} from '@/api/settings'

function embedding(overrides: Partial<EmbeddingSettings> = {}): EmbeddingSettings {
  return {
    provider: 'openai',
    model: 'text-embedding-3-small',
    dimensions: 512,
    openai_key_configured: false,
    ollama_base_url: 'http://localhost:11434',
    configured: false,
    available_providers: ['openai', 'ollama'],
    available_models: [
      { id: 'text-embedding-3-small', label: 'text-embedding-3-small (recommended)' },
      { id: 'text-embedding-3-large', label: 'text-embedding-3-large' },
    ],
    ...overrides,
  }
}

function speech(overrides: Partial<SpeechSettings> = {}): SpeechSettings {
  return {
    deepgram_key_configured: false,
    deepgram_model: 'nova-3',
    available_models: [{ id: 'nova-3', label: 'Nova 3 (recommended)' }],
    ...overrides,
  }
}

function renderView() {
  return render(
    <MemoryRouter>
      <SettingsView />
    </MemoryRouter>
  )
}

function coverage(overrides: Partial<EmbeddingCoverage> = {}): EmbeddingCoverage {
  return {
    model: 'text-embedding-3-small',
    total_assets: 530,
    embedded_assets: 442,
    pending_assets: 88,
    pending_segments: 12430,
    ...overrides,
  }
}

beforeEach(() => {
  vi.spyOn(speechSettingsApi, 'get').mockResolvedValue(speech())
  vi.spyOn(embeddingsApi, 'status').mockResolvedValue(coverage())
})

afterEach(() => {
  useActivityStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SettingsView', () => {
  it('says how much of the library semantic search cannot see', async () => {
    // The gap this whole feature exists to close: adding a key does nothing for the
    // content that was already here, and nothing used to say so.
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ configured: true, openai_key_configured: true })
    )
    renderView()

    expect(await screen.findByText(/88 of 530 assets/)).toBeInTheDocument()
    expect(screen.getByText(/12,430 transcript segments/)).toBeInTheDocument()
  })

  it('never shows a price', async () => {
    // There is no UsageEvent model and no pricing.py in this repo. An invented
    // estimate would be worse than no estimate, so this asserts the absence.
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ configured: true, openai_key_configured: true })
    )
    renderView()

    await screen.findByText(/88 of 530 assets/)
    expect(screen.queryByText(/\$/)).toBeNull()
  })

  it('does not ask for coverage before a provider is configured', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ configured: false })
    )
    renderView()

    await screen.findByText(/semantic search is off/i)
    expect(embeddingsApi.status).not.toHaveBeenCalled()
  })

  it('starts a backfill', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ configured: true, openai_key_configured: true })
    )
    const backfill = vi.spyOn(embeddingsApi, 'backfill').mockResolvedValue({
      id: 'j1',
      kind: 'enrichment',
      action: 'backfill_embeddings',
      status: 'queued',
      stalled: false,
      stage: 'Queued',
      progress: 0,
      detail: '',
      asset_id: null,
      asset_name: '',
      model: 'text-embedding-3-small',
      error_message: null,
      created_at: '2026-09-14T10:00:00Z',
      updated_at: '2026-09-14T10:00:00Z',
    })
    renderView()

    await userEvent.click(await screen.findByRole('button', { name: /embed 88 assets/i }))

    expect(backfill).toHaveBeenCalled()
  })

  it('says so when there is nothing left to embed', async () => {
    vi.spyOn(embeddingsApi, 'status').mockResolvedValue(
      coverage({ pending_assets: 0, embedded_assets: 530, pending_segments: 0 })
    )
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ configured: true, openai_key_configured: true })
    )
    renderView()

    expect(await screen.findByText(/everything is embedded/i)).toBeInTheDocument()
  })

  it('offers a control for the embedding provider the search view points at', async () => {
    // The reason this panel exists: SearchView tells people to "add an embedding
    // provider in Settings", and for five milestones there was nothing here to add.
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(embedding())
    renderView()

    expect(await screen.findByLabelText(/provider/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/openai api key/i)).toBeInTheDocument()
  })

  it('says semantic search is off until a key is stored', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ configured: false })
    )
    renderView()

    expect(await screen.findByText(/semantic search is off/i)).toBeInTheDocument()
  })

  it('says semantic search is on once it is configured', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ configured: true, openai_key_configured: true })
    )
    renderView()

    expect(await screen.findByText(/semantic search is on/i)).toBeInTheDocument()
  })

  it('saves a pasted key and clears the box', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(embedding())
    const update = vi
      .spyOn(embeddingSettingsApi, 'update')
      .mockResolvedValue(embedding({ openai_key_configured: true, configured: true }))
    renderView()

    const input = await screen.findByLabelText(/openai api key/i)
    await userEvent.type(input, 'sk-test-key')
    await userEvent.click(screen.getByRole('button', { name: /save openai key/i }))

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ openai_api_key: 'sk-test-key' })
    )
    // The key must not linger in the DOM after it has been stored.
    expect(input).toHaveValue('')
  })

  it('clears a stored key with an empty string, not by omitting it', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ openai_key_configured: true, configured: true })
    )
    const update = vi.spyOn(embeddingSettingsApi, 'update').mockResolvedValue(embedding())
    renderView()

    await userEvent.click(
      await screen.findByRole('button', { name: /remove the stored key/i })
    )

    await waitFor(() => expect(update).toHaveBeenCalledWith({ openai_api_key: '' }))
  })

  it('swaps the key field for an address when the provider is local', async () => {
    // Ollama needs no credential, so asking for one would be a lie.
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ provider: 'ollama', configured: true, model: 'nomic-embed-text' })
    )
    renderView()

    expect(await screen.findByLabelText(/^ollama address$/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/openai api key/i)).not.toBeInTheDocument()
  })

  it('still shows the Deepgram panel', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(embedding())
    renderView()

    expect(await screen.findByLabelText(/^api key$/i)).toBeInTheDocument()
  })

  it('surfaces a failure to load rather than rendering an empty panel', async () => {
    // An opaque rejection still has to say something: apiErrorMessage falls back when
    // the failure carries no message of its own.
    vi.spyOn(embeddingSettingsApi, 'get').mockRejectedValue({})
    renderView()

    expect(await screen.findByText(/could not load settings/i)).toBeInTheDocument()
  })
})
