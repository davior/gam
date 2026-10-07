import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GenerateForm, { type GeneratePreset } from '@/components/GenerateForm'
import type { Asset } from '@/api/assets'
import { generateApi, type GenerationModel } from '@/api/generate'
import { activityApi, type ActivityJob } from '@/api/transcripts'
import { useActivityStore } from '@/stores/activity'
import { useGenerationStore } from '@/stores/generation'
import { makeGenerationModel, noAttribution } from '@/test-fixtures'

function image(id: string, name = id): Asset {
  return {
    id,
    name,
    description: null,
    summary: null,
    ...noAttribution,
    asset_type: 'image',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: `${id}.jpg`,
    mime_type: 'image/jpeg',
    file_format: 'jpg',
    size_bytes: 100,
    duration_seconds: null,
    width: 800,
    height: 600,
    codec: null,
    file_url: `/media/u/${id}.jpg`,
    thumb_url: `/media/u/${id}.thumb.jpg`,
    missing: false,
    tags: [],
    upload_date: '2026-10-01T00:00:00Z',
    modified_date: '2026-10-01T00:00:00Z',
    metadata_modified_date: '2026-10-01T00:00:00Z',
  }
}

function queuedJob(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: 'g1',
    kind: 'enrichment',
    action: 'generate',
    status: 'queued',
    stalled: false,
    stage: 'Queued',
    progress: 0,
    detail: '',
    asset_id: null,
    asset_name: 'a red fox in snow',
    model: 'fal-ai/flux/dev',
    result_asset_id: null,
    result_asset_ids: [],
    error_message: null,
    created_at: '2026-10-07T10:00:00Z',
    updated_at: '2026-10-07T10:00:00Z',
    ...overrides,
  }
}

function codedError(code: string, message: string) {
  const error = new AxiosError('failed')
  // @ts-expect-error - a minimal response is all apiErrorCode and apiErrorMessage read
  error.response = { data: { detail: { code, message } } }
  return error
}

const SCHNELL = makeGenerationModel({
  id: 'schnell',
  endpoint_id: 'fal-ai/flux/schnell',
  label: 'FLUX schnell',
  sort_order: 20,
})
const DEV = makeGenerationModel({
  id: 'dev',
  endpoint_id: 'fal-ai/flux/dev',
  label: 'FLUX dev',
  sort_order: 10,
  note: 'Good with text',
  unit_price: 0.025,
  price_unit: 'megapixel',
  price_currency: 'USD',
  options: { image_sizes: ['square_hd', 'landscape_16_9'], supports_seed: true },
})
const EDIT_ONE = makeGenerationModel({
  id: 'kontext',
  endpoint_id: 'fal-ai/flux-pro/kontext',
  kind: 'image_to_image',
  label: 'Kontext',
  image_field: 'image_url',
  max_images: 1,
})
const EDIT_MANY = makeGenerationModel({
  id: 'seedream',
  endpoint_id: 'fal-ai/bytedance/seedream/v4.5/edit',
  kind: 'image_to_image',
  label: 'Seedream',
  sort_order: 5,
  image_field: 'image_urls',
  image_field_is_list: true,
  max_images: 10,
})
const KLING = makeGenerationModel({
  id: 'kling',
  endpoint_id: 'fal-ai/kling-video/v2.5-turbo/pro/image-to-video',
  kind: 'image_to_video',
  label: 'Kling',
  image_field: 'image_url',
  max_images: 1,
  end_image_field: 'tail_image_url',
  options: { durations: ['5', '10'], supports_negative_prompt: true },
})
const VEO = makeGenerationModel({
  id: 'veo',
  endpoint_id: 'fal-ai/veo3.1/fast/image-to-video',
  kind: 'image_to_video',
  label: 'Veo',
  image_field: 'image_url',
  max_images: 1,
  extra_params: { generate_audio: false },
  options: { durations: ['8s'], supports_audio: true },
})

const CATALOGUE = [SCHNELL, DEV, EDIT_ONE, EDIT_MANY, KLING, VEO]

function renderForm(
  props: { bases?: Asset[]; preset?: GeneratePreset } = {},
  catalogue: GenerationModel[] = CATALOGUE
) {
  useGenerationStore.setState({ models: catalogue, loaded: true })
  return render(
    <MemoryRouter>
      <GenerateForm title="Generate" {...props} />
    </MemoryRouter>
  )
}

const form = () => screen.getByRole('form', { name: 'Generate' })
const modelNames = () =>
  within(screen.getByLabelText('Model'))
    .getAllByRole('option')
    .map((option) => option.textContent)

beforeEach(() => {
  // Here rather than in afterEach: the form subscribes to both, and resetting them while
  // the last test's form is still mounted is a render outside act().
  useActivityStore.getState().reset()
  useGenerationStore.getState().reset()
  // Mounting asks for the catalogue; `renderForm` seeds it, so this only guards against
  // a test that forgot to reaching for a server that is not there.
  vi.spyOn(generateApi, 'models').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('GenerateForm: what it offers', () => {
  it('makes text → image from nothing, and offers only those models', () => {
    renderForm()

    expect(screen.queryByRole('radio')).toBeNull()
    expect(modelNames()).toEqual(['FLUX dev', 'FLUX schnell'])
  })

  it('offers image → image or image → video for one base', async () => {
    const user = userEvent.setup()
    renderForm({ bases: [image('b1')] })

    expect(screen.getByRole('radio', { name: 'Image → image' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Image → video' })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'Text → image' })).toBeNull()
    expect(modelNames()).toEqual(['Kontext', 'Seedream'])

    await user.click(screen.getByRole('radio', { name: 'Image → video' }))
    expect(modelNames()).toEqual(['Kling', 'Veo'])
  })

  it('stops offering video past a start and an end frame', () => {
    renderForm({ bases: [image('b1'), image('b2'), image('b3')] })

    expect(screen.queryByRole('radio', { name: 'Image → video' })).toBeNull()
  })

  it('leaves out an edit model that cannot take this many bases', () => {
    // Kontext takes one image. Offering it for two is offering a 422.
    renderForm({ bases: [image('b1'), image('b2')] })

    expect(modelNames()).toEqual(['Seedream'])
  })

  it('says so when no model can take that many', () => {
    renderForm({ bases: Array.from({ length: 11 }, (_, i) => image(`b${i}`)) })

    expect(screen.getByText('No image → image model takes 11 base images.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled()
  })

  it('offers only video models that take an end frame for two bases', async () => {
    const user = userEvent.setup()
    renderForm({ bases: [image('b1'), image('b2')] })

    await user.click(screen.getByRole('radio', { name: 'Image → video' }))

    expect(modelNames()).toEqual(['Kling'])
  })

  it('shows the chosen model’s note and list price', () => {
    renderForm()

    expect(
      screen.getByText('Good with text · ≈ $0.025 per megapixel, list price')
    ).toBeVisible()
  })
})

describe('GenerateForm: option controls', () => {
  const ALL = makeGenerationModel({
    id: 'all',
    label: 'Everything',
    sort_order: 1,
    options: {
      aspect_ratios: ['16:9', '1:1'],
      image_sizes: ['square_hd'],
      durations: [6, 10],
      resolutions: ['720p'],
      supports_seed: true,
      supports_negative_prompt: true,
      supports_audio: true,
      max_outputs: 4,
    },
  })
  const NONE = makeGenerationModel({ id: 'none', label: 'Nothing', sort_order: 0 })

  const CONTROLS = [
    'Aspect ratio',
    'Image size',
    'Duration',
    'Resolution',
    'Seed',
    'Negative prompt',
    'Generate audio',
    'Number of outputs',
  ]

  it('renders none that the model does not declare', () => {
    renderForm({}, [NONE, ALL])

    for (const label of CONTROLS) expect(screen.queryByLabelText(label)).toBeNull()
  })

  it('renders each one the model declares', async () => {
    const user = userEvent.setup()
    renderForm({}, [NONE, ALL])

    await user.selectOptions(screen.getByLabelText('Model'), 'all')

    for (const label of CONTROLS) expect(screen.getByLabelText(label)).toBeVisible()
    expect(
      within(screen.getByLabelText('Number of outputs'))
        .getAllByRole('option')
        .map((o) => o.textContent)
    ).toEqual(['1', '2', '3', '4'])
  })

  it('shows the catalogue’s audio default without sending it as a choice', async () => {
    // Veo's row turns audio off because it raises the price by half. The box reflects
    // that, and an untouched box sends null so the row's default stays in charge.
    const user = userEvent.setup()
    const start = vi.spyOn(generateApi, 'start').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    renderForm({ bases: [image('b1')] })

    await user.click(screen.getByRole('radio', { name: 'Image → video' }))
    await user.selectOptions(screen.getByLabelText('Model'), 'veo')
    expect(screen.getByLabelText('Generate audio')).not.toBeChecked()

    await user.type(screen.getByLabelText('Prompt'), 'waves')
    await user.click(screen.getByRole('button', { name: 'Generate' }))

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0][0].params.generate_audio).toBeNull()
  })
})

describe('GenerateForm: submitting', () => {
  it('sends exactly what was chosen, and null for the rest', async () => {
    const user = userEvent.setup()
    const start = vi.spyOn(generateApi, 'start').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    renderForm()

    await user.type(screen.getByLabelText('Prompt'), '  a red fox in snow ')
    await user.selectOptions(screen.getByLabelText('Image size'), 'landscape_16_9')
    await user.type(screen.getByLabelText('Seed'), '42')
    await user.click(screen.getByRole('button', { name: 'Generate' }))

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0][0]).toEqual({
      model_id: 'dev',
      prompt: 'a red fox in snow',
      base_asset_ids: [],
      end_frame_asset_id: null,
      params: {
        aspect_ratio: null,
        image_size: 'landscape_16_9',
        duration: null,
        resolution: null,
        seed: 42,
        negative_prompt: null,
        generate_audio: null,
        num_outputs: 1,
      },
    })
  })

  it('keeps a value the type the endpoint declared it as', async () => {
    // Wan wants `6`, Kling wants `"5"`: the server compares against the row's list
    // verbatim, so a number that came back as a string would be refused.
    const user = userEvent.setup()
    const wan = makeGenerationModel({
      id: 'wan',
      kind: 'image_to_video',
      label: 'Wan',
      image_field: 'image_url',
      max_images: 1,
      options: { durations: [6, 10] },
    })
    const start = vi.spyOn(generateApi, 'start').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    renderForm({ bases: [image('b1')] }, [wan])

    await user.click(screen.getByRole('radio', { name: 'Image → video' }))
    await user.selectOptions(screen.getByLabelText('Duration'), '6')
    await user.type(screen.getByLabelText('Prompt'), 'pan left')
    await user.click(screen.getByRole('button', { name: 'Generate' }))

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0][0].params.duration).toBe(6)
  })

  it('sends every base, in order, for image → image', async () => {
    const user = userEvent.setup()
    const start = vi.spyOn(generateApi, 'start').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    renderForm({ bases: [image('b2'), image('b1')] })

    await user.type(screen.getByLabelText('Prompt'), 'merge them')
    await user.click(screen.getByRole('button', { name: 'Generate' }))

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0][0]).toMatchObject({
      model_id: 'seedream',
      base_asset_ids: ['b2', 'b1'],
      end_frame_asset_id: null,
    })
  })

  it('labels the bases as frames for video, and sends the second as the end frame', async () => {
    const user = userEvent.setup()
    const start = vi.spyOn(generateApi, 'start').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    renderForm({ bases: [image('first', 'Dawn'), image('last', 'Dusk')] })

    const bases = within(screen.getByRole('list', { name: 'Base images' }))
    expect(bases.getByText('Base 1')).toBeVisible()

    await user.click(screen.getByRole('radio', { name: 'Image → video' }))

    const items = bases.getAllByRole('listitem')
    expect(within(items[0]).getByText('Start frame')).toBeVisible()
    expect(within(items[0]).getByText('Dawn')).toBeVisible()
    expect(within(items[1]).getByText('End frame')).toBeVisible()
    expect(within(items[1]).getByText('Dusk')).toBeVisible()

    await user.type(screen.getByLabelText('Prompt'), 'day turns to night')
    await user.click(screen.getByRole('button', { name: 'Generate' }))

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0][0]).toMatchObject({
      model_id: 'kling',
      base_asset_ids: ['first'],
      end_frame_asset_id: 'last',
    })
  })

  it('confirms, keeps the prompt, and tells the activity feed', async () => {
    const user = userEvent.setup()
    vi.spyOn(generateApi, 'start').mockResolvedValue(queuedJob())
    const poll = vi.spyOn(activityApi, 'list').mockResolvedValue([queuedJob()])
    renderForm()

    await user.type(screen.getByLabelText('Prompt'), 'a red fox in snow')
    await user.click(screen.getByRole('button', { name: 'Generate' }))

    expect(await within(form()).findByRole('status')).toHaveTextContent(
      'Queued “a red fox in snow” — follow it under background activity.'
    )
    // Kept: the next thing anyone does is adjust the wording and go again.
    expect(screen.getByLabelText('Prompt')).toHaveValue('a red fox in snow')
    await waitFor(() => expect(poll).toHaveBeenCalled())
  })

  it('points at Settings when there is no fal.ai key', async () => {
    const user = userEvent.setup()
    vi.spyOn(generateApi, 'start').mockRejectedValue(
      codedError('fal_key_missing', 'Add a fal.ai key in Settings first')
    )
    renderForm()

    await user.type(screen.getByLabelText('Prompt'), 'a fox')
    await user.click(screen.getByRole('button', { name: 'Generate' }))

    expect(
      await screen.findByRole('link', { name: /add one in settings/i })
    ).toHaveAttribute('href', '/settings')
    expect(screen.getByRole('alert')).toHaveTextContent(/needs a fal\.ai key/i)
  })

  it('shows any other refusal in the server’s own words', async () => {
    const user = userEvent.setup()
    vi.spyOn(generateApi, 'start').mockRejectedValue(
      codedError('invalid_generation', 'The prompt is longer than 4,000 characters')
    )
    renderForm()

    await user.type(screen.getByLabelText('Prompt'), 'a fox')
    await user.click(screen.getByRole('button', { name: 'Generate' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The prompt is longer than 4,000 characters'
    )
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('cannot be submitted without a prompt', () => {
    renderForm()

    expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled()
  })
})

describe('GenerateForm: pre-filled', () => {
  it('starts from a stored generation’s model, prompt and choices', async () => {
    const user = userEvent.setup()
    const start = vi.spyOn(generateApi, 'start').mockResolvedValue(queuedJob())
    vi.spyOn(activityApi, 'list').mockResolvedValue([])
    renderForm({
      bases: [image('b1')],
      preset: {
        model: KLING.endpoint_id,
        kind: 'image_to_video',
        prompt: 'the tide comes in',
        parameters: { duration: '10', negative_prompt: 'blur', cfg_scale: 0.5 },
      },
    })

    expect(screen.getByRole('radio', { name: 'Image → video' })).toBeChecked()
    expect(screen.getByLabelText('Model')).toHaveValue('kling')
    expect(screen.getByLabelText('Prompt')).toHaveValue('the tide comes in')
    expect(screen.getByLabelText('Duration')).toHaveValue('10')
    expect(screen.getByLabelText('Negative prompt')).toHaveValue('blur')

    await user.click(screen.getByRole('button', { name: 'Generate' }))

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0][0].params).toMatchObject({
      duration: '10',
      negative_prompt: 'blur',
    })
  })

  it('says so when the model it was made with is no longer offered', () => {
    renderForm({
      preset: {
        model: 'fal-ai/imagen4/preview',
        kind: 'text_to_image',
        prompt: 'a fox',
        parameters: {},
      },
    })

    expect(screen.getByText(/fal-ai\/imagen4\/preview, which made this/)).toBeVisible()
  })
})
