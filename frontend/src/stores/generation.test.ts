import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { generateApi, type GenerationModel } from '@/api/generate'
import { useGenerationStore } from '@/stores/generation'
import { makeGenerationModel } from '@/test-fixtures'

beforeEach(() => {
  useGenerationStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useGenerationStore', () => {
  it('loads the active catalogue', async () => {
    const models = vi
      .spyOn(generateApi, 'models')
      .mockResolvedValue([makeGenerationModel()])

    await useGenerationStore.getState().load()

    // Active rows only — inactive ones are the Settings panel's business.
    expect(models).toHaveBeenCalledWith()
    const state = useGenerationStore.getState()
    expect(state.models.map((m) => m.id)).toEqual(['m1'])
    expect(state.loaded).toBe(true)
  })

  it('asks once however many forms mount', async () => {
    const models = vi.spyOn(generateApi, 'models').mockResolvedValue([])

    useGenerationStore.getState().ensureLoaded()
    useGenerationStore.getState().ensureLoaded()
    await vi.waitFor(() => expect(useGenerationStore.getState().loaded).toBe(true))
    useGenerationStore.getState().ensureLoaded()

    expect(models).toHaveBeenCalledTimes(1)
  })

  it('says why when the catalogue cannot be read', async () => {
    vi.spyOn(generateApi, 'models').mockRejectedValue(new Error('Network Error'))

    await useGenerationStore.getState().load()

    expect(useGenerationStore.getState().error).toBe('Network Error')
    expect(useGenerationStore.getState().loaded).toBe(false)
  })

  it('empties on reset, so the next person fetches their own', async () => {
    vi.spyOn(generateApi, 'models').mockResolvedValue([makeGenerationModel()])
    await useGenerationStore.getState().load()

    useGenerationStore.getState().reset()

    const state = useGenerationStore.getState()
    expect(state.models).toEqual([])
    expect(state.loaded).toBe(false)
  })

  it('drops a response that lands after a reset', async () => {
    // A sign-out while the request is out must not have it repopulate the store.
    let answer: (models: GenerationModel[]) => void = () => {}
    vi.spyOn(generateApi, 'models').mockImplementation(
      () => new Promise((resolve) => (answer = resolve))
    )

    const loading = useGenerationStore.getState().load()
    useGenerationStore.getState().reset()
    answer([makeGenerationModel()])
    await loading

    expect(useGenerationStore.getState().models).toEqual([])
    expect(useGenerationStore.getState().loaded).toBe(false)
  })
})
