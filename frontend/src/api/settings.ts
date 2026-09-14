import client from '@/api/client'

/**
 * Per-user settings: the credentials and provider choices enrichment runs on.
 *
 * A secret is written but never read back — the responses say whether a key is
 * configured, not what it is.
 */

interface DataResponse<T> {
  data: T
}

export interface SpeechSettings {
  deepgram_key_configured: boolean
  deepgram_model: string
  available_models: Array<{ id: string; label: string }>
}

export const speechSettingsApi = {
  get(): Promise<SpeechSettings> {
    return client
      .get<DataResponse<SpeechSettings>>('/settings/speech')
      .then((r) => r.data.data)
  },

  update(changes: {
    /** '' clears the stored key; omit the field to leave it alone. */
    deepgram_api_key?: string
    deepgram_model?: string
  }): Promise<SpeechSettings> {
    return client
      .put<DataResponse<SpeechSettings>>('/settings/speech', changes)
      .then((r) => r.data.data)
  },
}

export interface EmbeddingSettings {
  provider: string
  model: string
  dimensions: number
  openai_key_configured: boolean
  ollama_base_url: string
  /**
   * Whether the chosen provider has everything it needs to actually run. Not the same
   * as a key being present: Ollama is local and needs no credential at all.
   */
  configured: boolean
  available_providers: string[]
  available_models: Array<{ id: string; label: string }>
}

export const embeddingSettingsApi = {
  get(): Promise<EmbeddingSettings> {
    return client
      .get<DataResponse<EmbeddingSettings>>('/settings/embeddings')
      .then((r) => r.data.data)
  },

  update(changes: {
    provider?: string
    model?: string
    dimensions?: number
    /** '' clears the stored key; omit the field to leave it alone. */
    openai_api_key?: string
    ollama_base_url?: string
  }): Promise<EmbeddingSettings> {
    return client
      .put<DataResponse<EmbeddingSettings>>('/settings/embeddings', changes)
      .then((r) => r.data.data)
  },
}
