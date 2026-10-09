import type { Asset } from '@/api/assets'
import type { GenerationModel, GenerationOptions } from '@/api/generate'

/**
 * The attribution half of an `Asset`, empty — and M8's `generation`, null.
 *
 * Eight test files build their own asset literals, and M10 added ten fields to the shape.
 * Spreading this into each keeps them compiling without duplicating the list eight times —
 * and means the next field added to `Asset` is one edit here rather than eight. M8's
 * `generation` is that next field, so it rides along here despite the name: an asset no
 * one generated is the default every one of those files wants.
 *
 * Deliberately only these fields rather than a whole `makeAsset` factory: each of those
 * files varies a different part of the asset, and replacing their factories with one
 * shared default would flatten distinctions their tests depend on.
 */
export const noAttribution: Pick<
  Asset,
  | 'source_url'
  | 'creator'
  | 'publisher'
  | 'source_title'
  | 'published_date'
  | 'retrieved_at'
  | 'license'
  | 'credit_line'
  | 'credit'
  | 'attribution_inherited'
  | 'generation'
> = {
  source_url: null,
  creator: null,
  publisher: null,
  source_title: null,
  published_date: null,
  retrieved_at: null,
  license: null,
  credit_line: null,
  credit: '',
  attribution_inherited: [],
  generation: null,
}

/**
 * A catalogue row that declares nothing: text → image, no options, one output.
 *
 * A whole factory this time, unlike `noAttribution` — the generation tests are about
 * which controls a row's declarations switch on, so each one should start from a row
 * that switches on none and name exactly the ones it is testing.
 */
export function makeGenerationModel(
  overrides: Omit<Partial<GenerationModel>, 'options'> & {
    options?: Partial<GenerationOptions>
  } = {}
): GenerationModel {
  return {
    id: 'm1',
    endpoint_id: 'fal-ai/flux/schnell',
    kind: 'text_to_image',
    label: 'FLUX.1 [schnell]',
    note: '',
    sort_order: 0,
    is_active: true,
    image_field: null,
    image_field_is_list: false,
    max_images: 0,
    end_image_field: null,
    extra_params: {},
    unit_price: null,
    price_unit: null,
    price_currency: null,
    created_at: '2026-10-01T00:00:00',
    updated_at: '2026-10-01T00:00:00',
    ...overrides,
    options: {
      aspect_ratios: [],
      image_sizes: [],
      durations: [],
      resolutions: [],
      supports_seed: false,
      supports_negative_prompt: false,
      supports_audio: false,
      max_outputs: 1,
      ...overrides.options,
    },
  }
}
