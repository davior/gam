import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  assistantApi,
  type AssistantSessionDetail,
  type AssistantTurn,
} from '@/api/assistant'
import { useAssistantStore } from '@/stores/assistant'

const READY = { available: true, ready: true, model: 'Claude', message: null }

function turn(overrides: Partial<AssistantTurn> = {}): AssistantTurn {
  return {
    id: 't1',
    session_id: 's1',
    status: 'queued',
    stage: '',
    detail: '',
    error: null,
    prompt: 'Tidy this',
    focus_asset_id: null,
    persona_id: null,
    steps: [],
    reply: '',
    proposal: null,
    outcome: '',
    cost: null,
    created_at: '2026-11-02T10:00:00',
    ...overrides,
  }
}

function detail(turns: AssistantTurn[], messages = []): AssistantSessionDetail {
  return {
    id: 's1',
    title: 'Tidy this',
    created_at: '2026-11-02T10:00:00',
    updated_at: '2026-11-02T10:00:00',
    messages,
    turns,
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(assistantApi, 'status').mockResolvedValue(READY)
  vi.spyOn(assistantApi, 'listSessions').mockResolvedValue([])
  vi.spyOn(assistantApi, 'personas').mockResolvedValue([])
  useAssistantStore.getState().reset()
})

afterEach(() => {
  useAssistantStore.getState().reset()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('assistant store', () => {
  it('starts a conversation on the first message and follows the turn to its end', async () => {
    const created = {
      id: 's1',
      title: '',
      created_at: '2026-11-02T10:00:00',
      updated_at: '2026-11-02T10:00:00',
    }
    vi.spyOn(assistantApi, 'createSession').mockResolvedValue(created)
    const ask = vi.spyOn(assistantApi, 'ask').mockResolvedValue(turn())
    const running = turn({ status: 'processing', steps: [{ kind: 'search', text: 'x' }] })
    const finished = turn({ status: 'done', reply: 'All tidy.' })
    const poll = vi
      .spyOn(assistantApi, 'getTurn')
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(finished)
    vi.spyOn(assistantApi, 'getSession').mockResolvedValue(detail([finished]))

    useAssistantStore.getState().setFocus({ id: 'a1', name: 'Interview' })
    await useAssistantStore.getState().send('  Tidy this  ')

    // The open asset goes with the message, so "this" means something.
    expect(ask).toHaveBeenCalledWith('s1', 'Tidy this', 'a1', null)
    expect(useAssistantStore.getState().active?.turns[0].status).toBe('queued')

    await vi.advanceTimersByTimeAsync(1500)
    expect(useAssistantStore.getState().active?.turns[0].steps).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1500)
    expect(poll).toHaveBeenCalledTimes(2)
    expect(useAssistantStore.getState().active?.turns[0].reply).toBe('All tidy.')

    // Finished: no more polling.
    await vi.advanceTimersByTimeAsync(10000)
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('shows why a message could not be sent', async () => {
    vi.spyOn(assistantApi, 'createSession').mockResolvedValue({
      id: 's1',
      title: '',
      created_at: '',
      updated_at: '',
    })
    vi.spyOn(assistantApi, 'ask').mockRejectedValue({
      isAxiosError: true,
      response: {
        data: { detail: { code: 'no_provider', message: 'No AI model is set up.' } },
      },
    })
    await useAssistantStore.getState().send('hello')
    expect(useAssistantStore.getState().error).toBe('No AI model is set up.')
    expect(useAssistantStore.getState().sending).toBe(false)
  })

  it('picks a running turn back up when a conversation is opened', async () => {
    const running = turn({ status: 'processing' })
    vi.spyOn(assistantApi, 'getSession').mockResolvedValue(detail([running]))
    const poll = vi
      .spyOn(assistantApi, 'getTurn')
      .mockResolvedValue(turn({ status: 'done', reply: 'Back.' }))

    await useAssistantStore.getState().select('s1')
    expect(poll).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1500)
    expect(poll).toHaveBeenCalledWith('t1')
  })

  it('drops an answer that lands after a reset', async () => {
    let release: (value: AssistantSessionDetail) => void = () => {}
    vi.spyOn(assistantApi, 'getSession').mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      })
    )
    const opening = useAssistantStore.getState().select('s1')
    useAssistantStore.getState().reset()
    release(detail([turn({ status: 'done' })]))
    await opening
    // Signing out must not leave the last person's conversation to be shown to the next.
    expect(useAssistantStore.getState().active).toBeNull()
  })

  it('stops polling and forgets everything on reset', async () => {
    vi.spyOn(assistantApi, 'getSession').mockResolvedValue(
      detail([turn({ status: 'processing' })])
    )
    const poll = vi.spyOn(assistantApi, 'getTurn').mockResolvedValue(turn())
    await useAssistantStore.getState().select('s1')
    useAssistantStore.getState().reset()
    await vi.advanceTimersByTimeAsync(5000)
    expect(poll).not.toHaveBeenCalled()
    expect(useAssistantStore.getState().active).toBeNull()
  })

  it('asks the server to apply only the chosen assets, then refreshes the turn', async () => {
    const apply = vi
      .spyOn(assistantApi, 'apply')
      .mockResolvedValue([
        { asset_id: 'a1', asset_name: 'One', applied: ['name'], skipped: [] },
      ])
    vi.spyOn(assistantApi, 'getTurn').mockResolvedValue(
      turn({ status: 'done', outcome: 'applied' })
    )
    vi.spyOn(assistantApi, 'getSession').mockResolvedValue(
      detail([turn({ status: 'done' })])
    )
    await useAssistantStore.getState().select('s1')
    await useAssistantStore.getState().apply('t1', { assetIds: ['a1'] })
    expect(apply).toHaveBeenCalledWith('t1', { assetIds: ['a1'] })
    expect(useAssistantStore.getState().active?.turns[0].outcome).toBe('applied')
    expect(useAssistantStore.getState().applied?.[0].applied).toEqual(['name'])
  })

  it('asks as the chosen persona, and as none once it is cleared', async () => {
    vi.spyOn(assistantApi, 'createSession').mockResolvedValue({
      id: 's1',
      title: '',
      created_at: '',
      updated_at: '',
    })
    const ask = vi.spyOn(assistantApi, 'ask').mockResolvedValue(turn())
    vi.spyOn(assistantApi, 'getTurn').mockResolvedValue(turn({ status: 'done' }))
    vi.spyOn(assistantApi, 'getSession').mockResolvedValue(
      detail([turn({ status: 'done' })])
    )

    useAssistantStore.getState().setPersona('p-arch')
    await useAssistantStore.getState().send('Is this original?')
    expect(ask).toHaveBeenLastCalledWith('s1', 'Is this original?', undefined, 'p-arch')

    useAssistantStore.getState().setPersona(null)
    await vi.advanceTimersByTimeAsync(3000)
    await useAssistantStore.getState().send('And now?')
    expect(ask).toHaveBeenLastCalledWith('s1', 'And now?', undefined, null)
  })

  it('drops a chosen persona the suite no longer offers', async () => {
    const persona = { id: 'p-arch', name: 'Archivist', description: '', avatar: null }
    const list = vi.spyOn(assistantApi, 'personas').mockResolvedValue([persona])
    useAssistantStore.getState().setPersona('p-arch')
    await useAssistantStore.getState().load()
    expect(useAssistantStore.getState().personaId).toBe('p-arch')

    // Deleted in the suite since: sending it again would end every turn with an error.
    list.mockResolvedValue([])
    await useAssistantStore.getState().load()
    expect(useAssistantStore.getState().personaId).toBeNull()
  })

  it('works without a picker when the personas cannot be fetched', async () => {
    vi.spyOn(assistantApi, 'personas').mockRejectedValue(new Error('down'))
    await useAssistantStore.getState().load()
    expect(useAssistantStore.getState().personas).toEqual([])
    expect(useAssistantStore.getState().status?.ready).toBe(true)
    expect(useAssistantStore.getState().error).toBeNull()
  })
})
