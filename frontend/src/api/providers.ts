import client from '@/api/client'

/**
 * The generative LLM providers a user has configured.
 *
 * Separate from the embedding provider in `api/settings.ts`, and deliberately so:
 * embeddings and generation are different capabilities with different provider sets.
 * Anthropic publishes no embeddings API at all; DeepSeek's chat models are text-only
 * while its Anthropic-compatible endpoint is the one that can search the web.
 *
 * As everywhere else, a key is written and never read back — the server reports whether
 * one is configured, not what it is.
 */

interface DataResponse<T> {
  data: T
}

interface ListResponse<T> {
  data: T[]
  total: number
}

export type ProviderType = 'anthropic' | 'openai' | 'deepseek' | 'ollama' | 'custom'

export interface AIProvider {
  id: string
  name: string
  provider_type: ProviderType
  base_url: string | null
  model: string
  max_tokens: number
  /** Whether this model accepts images. Gates the describe job, once it exists. */
  supports_images: boolean
  /**
   * Speak the Anthropic Messages protocol to this provider instead of its own. Only
   * meaningful for `deepseek` and `custom` — see `canUseAnthropicApi`.
   */
  use_anthropic_api: boolean
  extra_params: Record<string, unknown> | null
  enabled: boolean
  is_active: boolean
  api_key_configured: boolean
}

export interface ProviderInput {
  name: string
  provider_type: ProviderType
  /** '' clears the stored key on update; omit the field to leave it alone. */
  api_key?: string
  base_url?: string | null
  model: string
  max_tokens?: number
  supports_images?: boolean
  use_anthropic_api?: boolean
  extra_params?: Record<string, unknown>
  enabled?: boolean
  is_active?: boolean
}

export interface ProviderTestResult {
  success: boolean
  message: string
}

export interface ProviderTestInput {
  /** A saved provider, so its stored key is used — the browser never received it. */
  provider_id?: string
  provider_type: ProviderType
  api_key?: string
  base_url?: string | null
  model: string
  use_anthropic_api?: boolean
}

export const providersApi = {
  list(): Promise<AIProvider[]> {
    return client.get<ListResponse<AIProvider>>('/providers').then((r) => r.data.data)
  },

  create(input: ProviderInput): Promise<AIProvider> {
    return client
      .post<DataResponse<AIProvider>>('/providers', input)
      .then((r) => r.data.data)
  },

  update(id: string, changes: Partial<ProviderInput>): Promise<AIProvider> {
    return client
      .put<DataResponse<AIProvider>>(`/providers/${id}`, changes)
      .then((r) => r.data.data)
  },

  remove(id: string): Promise<void> {
    return client.delete(`/providers/${id}`).then(() => undefined)
  },

  activate(id: string): Promise<AIProvider> {
    return client
      .post<DataResponse<AIProvider>>(`/providers/${id}/activate`)
      .then((r) => r.data.data)
  },

  test(input: ProviderTestInput): Promise<ProviderTestResult> {
    return client
      .post<DataResponse<ProviderTestResult>>('/providers/test', input)
      .then((r) => r.data.data)
  },
}
