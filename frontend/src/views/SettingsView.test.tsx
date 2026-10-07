import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsView from '@/views/SettingsView'
import { embeddingsApi, type EmbeddingCoverage } from '@/api/embeddings'
import { generateApi } from '@/api/generate'
import { useActivityStore } from '@/stores/activity'
import { useAuthStore } from '@/stores/auth'
import {
  embeddingSettingsApi,
  generationSettingsApi,
  speechSettingsApi,
  type EmbeddingSettings,
  type SpeechSettings,
} from '@/api/settings'
import { providersApi } from '@/api/providers'
import { usageApi } from '@/api/usage'
import { makeGenerationModel } from '@/test-fixtures'

function embedding(overrides: Partial<EmbeddingSettings> = {}): EmbeddingSettings {
  return {
    provider: 'openai',
    model: 'text-embedding-3-small',
    dimensions: 512,
    openai_key_configured: false,
    base_url: '',
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
  // The view mounts the provider panel too, which loads on mount. Left unmocked it
  // reaches the real axios client and every test here logs a network failure.
  vi.spyOn(providersApi, 'list').mockResolvedValue([])
  // The view mounts the usage panel too, which loads on mount.
  vi.spyOn(usageApi, 'summary').mockResolvedValue({
    totals: {
      total_events: 0,
      priced_events: 0,
      cost: 0,
      currency: 'USD',
      estimated: true,
      tokens: 0,
      seconds: 0,
    },
    by_provider: [],
  })
  // And the generation panel, which reads the fal key's state and the catalogue.
  vi.spyOn(generationSettingsApi, 'get').mockResolvedValue({ fal_key_configured: false })
  vi.spyOn(generateApi, 'models').mockResolvedValue([])
  useAuthStore.getState().reset()
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

  it('shows no price in the semantic search panel', async () => {
    // This used to assert no price anywhere, because there was no pricing table to be
    // honest with. There is one now, but it covers generation — embedding spend is a
    // different axis and is not costed here, so this panel still shows counts only.
    // Scoped to the panel rather than the view: the usage panel below does show a
    // figure, and deliberately.
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ configured: true, openai_key_configured: true })
    )
    renderView()

    const heading = await screen.findByText(/^semantic search$/i)
    const panel = heading.closest('section')
    expect(panel).not.toBeNull()
    expect(within(panel as HTMLElement).queryByText(/\$/)).toBeNull()
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
      result_asset_id: null,
      result_asset_ids: [],
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

  // ─── the M5 carry-overs ────────────────────────────────────────────────────

  it('takes a model the suggestion list has never heard of', async () => {
    // It was a two-item <select> until now, so the only embedding models reachable from
    // this screen were the two someone typed into the backend months ago.
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ openai_key_configured: true, configured: true })
    )
    const update = vi.spyOn(embeddingSettingsApi, 'update').mockResolvedValue(embedding())
    renderView()

    const model = await screen.findByLabelText(/^embedding model$/i)
    await userEvent.clear(model)
    await userEvent.type(model, 'text-embedding-4-enormous')
    await userEvent.tab()

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ model: 'text-embedding-4-enormous' })
    )
  })

  it('does not save the model on every keystroke', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ openai_key_configured: true, configured: true })
    )
    const update = vi.spyOn(embeddingSettingsApi, 'update').mockResolvedValue(embedding())
    renderView()

    const model = await screen.findByLabelText(/^embedding model$/i)
    await userEvent.clear(model)
    await userEvent.type(model, 'nomic')

    expect(update).not.toHaveBeenCalled()
  })

  it('can point the embedder at an OpenAI-compatible endpoint', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ openai_key_configured: true, configured: true })
    )
    const update = vi.spyOn(embeddingSettingsApi, 'update').mockResolvedValue(embedding())
    renderView()

    const field = await screen.findByLabelText(/openai-compatible endpoint/i)
    await userEvent.type(field, 'https://gateway.example.com')
    await userEvent.click(
      screen.getByRole('button', { name: /save embedding endpoint/i })
    )

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ base_url: 'https://gateway.example.com' })
    )
  })

  it('does not offer that endpoint for Ollama, which has its own address', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(
      embedding({ provider: 'ollama', configured: true, model: 'nomic-embed-text' })
    )
    renderView()

    await screen.findByLabelText(/^ollama address$/i)
    expect(screen.queryByLabelText(/openai-compatible endpoint/i)).toBeNull()
  })

  it('renders the AI provider panel alongside the others', async () => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(embedding())
    renderView()

    expect(
      await screen.findByRole('button', { name: /add a provider/i })
    ).toBeInTheDocument()
  })
})

describe('SettingsView generation (M8)', () => {
  const DEV = makeGenerationModel({
    id: 'dev',
    endpoint_id: 'fal-ai/flux/dev',
    label: 'FLUX.1 [dev]',
    unit_price: 0.025,
    price_unit: 'megapixel',
    price_currency: 'USD',
  })
  const KLING = makeGenerationModel({
    id: 'kling',
    endpoint_id: 'fal-ai/kling-video/v2.5-turbo/pro/image-to-video',
    kind: 'image_to_video',
    label: 'Kling 2.5',
    image_field: 'image_url',
    max_images: 1,
    end_image_field: 'tail_image_url',
    options: { durations: ['5', '10'] },
  })
  const PARKED = makeGenerationModel({
    id: 'old',
    endpoint_id: 'fal-ai/imagen4/preview',
    label: 'Imagen 4',
    is_active: false,
  })

  function signInAs(is_admin: boolean) {
    useAuthStore.setState({
      user: { id: 'u1', username: 'davior', is_admin },
      status: 'authenticated',
    })
  }

  async function panel() {
    const heading = await screen.findByText('Image and video generation')
    return within(heading.closest('section') as HTMLElement)
  }

  beforeEach(() => {
    vi.spyOn(embeddingSettingsApi, 'get').mockResolvedValue(embedding())
  })

  it('saves a pasted fal.ai key and clears the box', async () => {
    const update = vi
      .spyOn(generationSettingsApi, 'update')
      .mockResolvedValue({ fal_key_configured: true })
    renderView()

    const input = await screen.findByLabelText('fal.ai API key')
    expect(input).toHaveAttribute('type', 'password')
    await userEvent.type(input, 'fal-secret')
    await userEvent.click(screen.getByRole('button', { name: 'Save fal.ai key' }))

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ fal_api_key: 'fal-secret' })
    )
    // The key must not linger in the DOM after it has been stored, and nothing about it
    // comes back — only that one is configured.
    expect(input).toHaveValue('')
    expect(input).toHaveAttribute('placeholder', 'A key is configured')
  })

  it('removes the stored fal.ai key with an empty string', async () => {
    vi.spyOn(generationSettingsApi, 'get').mockResolvedValue({ fal_key_configured: true })
    const update = vi
      .spyOn(generationSettingsApi, 'update')
      .mockResolvedValue({ fal_key_configured: false })
    renderView()

    await userEvent.click(
      await screen.findByRole('button', { name: 'Remove the stored fal.ai key' })
    )

    await waitFor(() => expect(update).toHaveBeenCalledWith({ fal_api_key: '' }))
  })

  it('shows everyone else the catalogue, read-only', async () => {
    signInAs(false)
    vi.spyOn(generateApi, 'models').mockResolvedValue([DEV, KLING])
    renderView()

    const generation = await panel()
    expect(await generation.findByText('FLUX.1 [dev]')).toBeInTheDocument()
    expect(generation.getByText('≈ $0.025 per megapixel, list price')).toBeInTheDocument()
    expect(generation.getByText('Kling 2.5')).toBeInTheDocument()
    expect(generateApi.models).toHaveBeenCalledWith(false)

    expect(generation.queryByRole('button', { name: /add a model/i })).toBeNull()
    expect(generation.queryByRole('button', { name: /edit/i })).toBeNull()
    expect(generation.queryByRole('button', { name: /delete/i })).toBeNull()
    expect(generation.queryByRole('button', { name: /deactivate/i })).toBeNull()
  })

  it('shows an admin the parked rows too, marked', async () => {
    signInAs(true)
    vi.spyOn(generateApi, 'models').mockResolvedValue([DEV, PARKED])
    renderView()

    const generation = await panel()
    expect(await generation.findByText('Imagen 4')).toBeInTheDocument()
    expect(generation.getByText('Inactive')).toBeInTheDocument()
    expect(generateApi.models).toHaveBeenCalledWith(true)
    expect(generation.getByRole('button', { name: 'Activate Imagen 4' })).toBeVisible()
  })

  it('lets an admin add a model, every field included', async () => {
    const user = userEvent.setup()
    signInAs(true)
    const created = makeGenerationModel({ id: 'hailuo', label: 'Hailuo 02' })
    const create = vi.spyOn(generateApi, 'createModel').mockResolvedValue(created)
    renderView()

    const generation = await panel()
    await user.click(await generation.findByRole('button', { name: 'Add a model' }))
    const editor = within(generation.getByRole('group', { name: 'New model' }))

    await user.type(
      editor.getByLabelText('Endpoint id'),
      'fal-ai/minimax/hailuo-02/standard/image-to-video'
    )
    await user.selectOptions(editor.getByLabelText('Kind'), 'image_to_video')
    await user.type(editor.getByLabelText('Label'), 'Hailuo 02')
    await user.type(editor.getByLabelText('Note'), 'Cheap')
    await user.clear(editor.getByLabelText('Sort order'))
    await user.type(editor.getByLabelText('Sort order'), '20')
    await user.type(editor.getByLabelText('Image field'), 'image_url')
    await user.type(editor.getByLabelText('End-frame field'), 'end_image_url')
    // A quoted value stays text, a bare number goes as a number — Hailuo wants `6`.
    await user.type(editor.getByLabelText('Durations'), '6, "10"')
    await user.type(editor.getByLabelText('Resolutions'), '512P, 768P')
    await user.click(editor.getByLabelText('Takes a negative prompt'))
    await user.click(editor.getByLabelText('Extra parameters'))
    await user.paste('{"prompt_optimizer": true}')
    await user.type(editor.getByLabelText('List price'), '0.045')
    await user.selectOptions(editor.getByLabelText('Price unit'), 'second')
    await user.click(editor.getByRole('button', { name: 'Save model' }))

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toEqual({
      endpoint_id: 'fal-ai/minimax/hailuo-02/standard/image-to-video',
      kind: 'image_to_video',
      label: 'Hailuo 02',
      note: 'Cheap',
      sort_order: 20,
      is_active: true,
      image_field: 'image_url',
      image_field_is_list: false,
      max_images: 1,
      end_image_field: 'end_image_url',
      options: {
        aspect_ratios: [],
        image_sizes: [],
        durations: [6, '10'],
        resolutions: ['512P', '768P'],
        supports_seed: false,
        supports_negative_prompt: true,
        supports_audio: false,
        max_outputs: 1,
      },
      extra_params: { prompt_optimizer: true },
      unit_price: 0.045,
      price_unit: 'second',
      price_currency: 'USD',
    })
    expect(await generation.findByText('Hailuo 02')).toBeInTheDocument()
  })

  it('refuses extra parameters that are not a JSON object', async () => {
    const user = userEvent.setup()
    signInAs(true)
    const create = vi.spyOn(generateApi, 'createModel')
    renderView()

    const generation = await panel()
    await user.click(await generation.findByRole('button', { name: 'Add a model' }))
    const editor = within(generation.getByRole('group', { name: 'New model' }))
    await user.type(editor.getByLabelText('Endpoint id'), 'fal-ai/flux/dev')
    await user.type(editor.getByLabelText('Label'), 'FLUX')
    await user.click(editor.getByLabelText('Extra parameters'))
    await user.paste('["not", "an", "object"]')
    await user.click(editor.getByRole('button', { name: 'Save model' }))

    expect(
      await generation.findByText(/extra parameters must be a json object/i)
    ).toBeInTheDocument()
    expect(create).not.toHaveBeenCalled()
  })

  it('edits a row without changing the type of its values', async () => {
    const user = userEvent.setup()
    signInAs(true)
    vi.spyOn(generateApi, 'models').mockResolvedValue([KLING])
    const update = vi.spyOn(generateApi, 'updateModel').mockResolvedValue(KLING)
    renderView()

    const generation = await panel()
    await user.click(await generation.findByRole('button', { name: 'Edit Kling 2.5' }))
    const editor = within(generation.getByRole('group', { name: 'Edit model' }))

    // Kling's durations are text; shown quoted so saving them back keeps them text.
    expect(editor.getByLabelText('Durations')).toHaveValue('"5", "10"')
    expect(editor.getByLabelText('End-frame field')).toHaveValue('tail_image_url')
    await user.click(editor.getByRole('button', { name: 'Save model' }))

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1))
    expect(update.mock.calls[0][0]).toBe('kling')
    expect(update.mock.calls[0][1]).toMatchObject({
      end_image_field: 'tail_image_url',
      options: { durations: ['5', '10'] },
    })
  })

  it('lets an admin deactivate and delete a model', async () => {
    const user = userEvent.setup()
    signInAs(true)
    vi.spyOn(generateApi, 'models').mockResolvedValue([DEV])
    const update = vi
      .spyOn(generateApi, 'updateModel')
      .mockResolvedValue({ ...DEV, is_active: false })
    const remove = vi.spyOn(generateApi, 'deleteModel').mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderView()

    const generation = await panel()
    await user.click(
      await generation.findByRole('button', { name: 'Deactivate FLUX.1 [dev]' })
    )
    await waitFor(() => expect(update).toHaveBeenCalledWith('dev', { is_active: false }))
    expect(await generation.findByText('Inactive')).toBeInTheDocument()

    await user.click(generation.getByRole('button', { name: 'Delete FLUX.1 [dev]' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('dev'))
    await waitFor(() => expect(generation.queryByText('FLUX.1 [dev]')).toBeNull())
  })
})
