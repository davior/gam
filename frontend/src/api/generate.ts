import client from '@/api/client'
import type { Asset } from '@/api/assets'
import type { ActivityJob } from '@/api/transcripts'

/**
 * M8: making new media with fal.ai, over `routers/generate.py`.
 *
 * Two halves. The catalogue is a global, admin-managed table of fal endpoints — data
 * rather than code, because fal renames and retires endpoint ids every few months — and
 * each row describes its endpoint's dialect: which image field it takes, which choices
 * it offers, and the exact values it accepts for each. Generating returns the queued job
 * rather than an asset, the way a URL import does: an image finishes on the first or
 * second poll of fal's queue, a video takes minutes, and either can make several assets.
 */

export type GenerationKind = 'text_to_image' | 'image_to_image' | 'image_to_video'

export const GENERATION_KINDS: GenerationKind[] = [
  'text_to_image',
  'image_to_image',
  'image_to_video',
]

export const KIND_LABELS: Record<GenerationKind, string> = {
  text_to_image: 'Text → image',
  image_to_image: 'Image → image',
  image_to_video: 'Image → video',
}

/**
 * A choice exactly as the endpoint takes it. Not normalised to a string: fal's endpoints
 * disagree on the type of the same idea — Kling wants `"5"`, Veo `"8s"`, Wan `6` — and
 * the server checks a submitted value against the row's list verbatim, so a value that
 * changed type on its way through the form would be refused as "not offered".
 */
export type OptionValue = string | number

export interface GenerationOptions {
  aspect_ratios: OptionValue[]
  image_sizes: OptionValue[]
  durations: OptionValue[]
  resolutions: OptionValue[]
  supports_seed: boolean
  supports_negative_prompt: boolean
  supports_audio: boolean
  /** 1–4 for images; always 1 for video. */
  max_outputs: number
}

export interface GenerationModel {
  id: string
  /** fal's own id, e.g. `fal-ai/flux/dev`. Unique across the catalogue. */
  endpoint_id: string
  kind: GenerationKind
  label: string
  note: string
  sort_order: number
  is_active: boolean
  /** The request field the base image goes in. Null for text → image. */
  image_field: string | null
  image_field_is_list: boolean
  /** 0 for text → image, at least 1 otherwise. */
  max_images: number
  /** Image → video only, and only for endpoints that take a last frame. */
  end_image_field: string | null
  options: GenerationOptions
  /** Merged into every request body first — a catalogue-level default. */
  extra_params: Record<string, unknown>
  /** The list price, the fallback when fal's pricing API cannot be asked. */
  unit_price: number | null
  price_unit: string | null
  price_currency: string | null
  created_at: string
  updated_at: string
}

/** What an admin writes. Everything but the server's own bookkeeping. */
export type GenerationModelInput = Omit<
  GenerationModel,
  'id' | 'created_at' | 'updated_at'
>

/**
 * Every user-facing choice, always sent in full. Null means "not chosen", which leaves the
 * endpoint's (or the catalogue's) default in charge; the server refuses a non-null value
 * the model does not declare, so the form only ever sets the ones it offered.
 */
export interface GenerationParams {
  aspect_ratio: OptionValue | null
  image_size: OptionValue | null
  duration: OptionValue | null
  resolution: OptionValue | null
  seed: number | null
  negative_prompt: string | null
  generate_audio: boolean | null
  num_outputs: number
}

export interface GenerationCreate {
  model_id: string
  prompt: string
  /** In order. For image → video, exactly one: the start frame. */
  base_asset_ids: string[]
  end_frame_asset_id: string | null
  params: GenerationParams
}

export interface RegenerateRequest {
  /** Null re-sends the stored prompt. */
  prompt: string | null
  /** Send the seed fal reported last time, when the model takes one. */
  reuse_seed: boolean
}

export interface GenerationSource {
  asset_id: string
  role: 'base' | 'end_frame'
  /** A snapshot taken at generation time, so a deleted base still reads as something. */
  name: string
}

/** What a generated asset remembers about how it was made. */
export interface AssetGeneration {
  /** The endpoint id, not a catalogue row id — rows come and go, endpoints are the record. */
  model: string
  kind: GenerationKind
  prompt: string
  /** The request body as sent, minus the prompt and the images — catalogue defaults
   *  included, so it still describes the run after the catalogue changes. */
  parameters: Record<string, unknown>
  sources: GenerationSource[]
  /** What fal reported back, when it reported one. A seed the request asked for but fal
   *  did not echo is in `parameters.seed` instead. */
  seed: number | null
  /** Null only for a row whose `ai_*` columns were written by hand — the server reads
   *  them leniently rather than refusing the asset. */
  generated_at: string | null
}

interface DataResponse<T> {
  data: T
}

interface ListResponse<T> {
  data: T[]
  total: number
}

export const generateApi = {
  /** Active rows only, unless an admin asks for the rest — the server ignores the flag
   *  for everybody else. Ordered by kind, sort order, label. */
  models(includeInactive = false): Promise<GenerationModel[]> {
    return client
      .get<ListResponse<GenerationModel>>('/generate/models', {
        params: { include_inactive: includeInactive },
      })
      .then((r) => r.data.data)
  },

  createModel(input: GenerationModelInput): Promise<GenerationModel> {
    return client
      .post<DataResponse<GenerationModel>>('/generate/models', input)
      .then((r) => r.data.data)
  },

  /** A PATCH: only the fields present change, and an explicit null clears one. */
  updateModel(
    id: string,
    changes: Partial<GenerationModelInput>
  ): Promise<GenerationModel> {
    return client
      .patch<DataResponse<GenerationModel>>(`/generate/models/${id}`, changes)
      .then((r) => r.data.data)
  },

  deleteModel(id: string): Promise<void> {
    return client.delete(`/generate/models/${id}`).then(() => undefined)
  },

  start(request: GenerationCreate): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>('/generate', request)
      .then((r) => r.data.data)
  },

  regenerate(assetId: string, request: RegenerateRequest): Promise<ActivityJob> {
    return client
      .post<DataResponse<ActivityJob>>(`/generate/${assetId}/regenerate`, request)
      .then((r) => r.data.data)
  },
}

/**
 * Whether an asset can be handed to fal as a base image.
 *
 * The server's rule is "an image that owns a file Pillow can read"; the last part it
 * checks itself and explains when it refuses. A clip never qualifies — it has no bytes
 * of its own, only a range of its parent's — and a missing file has nothing to send.
 */
export function canBeBase(asset: Asset): boolean {
  return (
    asset.asset_type === 'image' &&
    asset.source !== 'clip' &&
    !asset.missing &&
    asset.file_url !== null
  )
}

/**
 * "≈ $0.025 per megapixel, list price".
 *
 * Not `formatCost`: that rounds anything over a cent to two places, and a per-unit list
 * price of $0.025 shown as "$0.03" is a different price.
 */
export function formatListPrice(model: GenerationModel): string | null {
  if (model.unit_price === null) return null
  const currency = model.price_currency || 'USD'
  const symbol = currency === 'USD' ? '$' : `${currency} `
  const amount = model.unit_price.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })
  const unit = model.price_unit ? ` per ${model.price_unit}` : ''
  return `≈ ${symbol}${amount}${unit}, list price`
}
