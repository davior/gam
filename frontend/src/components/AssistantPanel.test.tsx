import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as configModule from '@/api/config'
import {
  assistantApi,
  type AssistantPersona,
  type AssistantStatus,
  type AssistantTurn,
  type ProposalItem,
} from '@/api/assistant'
import AssistantPanel from '@/components/AssistantPanel'
import { useAssistantStore } from '@/stores/assistant'

const READY: AssistantStatus = {
  available: true,
  ready: true,
  model: 'Claude',
  message: null,
}

function item(id: string, name: string, extra: Partial<ProposalItem> = {}): ProposalItem {
  return {
    asset_id: id,
    asset_name: name,
    before: { name, description: null, summary: null, tags: [] },
    add_tags: [],
    remove_tags: [],
    enrich: [],
    ...extra,
  }
}

function turn(overrides: Partial<AssistantTurn> = {}): AssistantTurn {
  return {
    id: 't1',
    session_id: 's1',
    status: 'done',
    stage: '',
    detail: '',
    error: null,
    prompt: 'Tidy these',
    focus_asset_id: null,
    persona_id: null,
    steps: [],
    reply: 'I would rename two.',
    proposal: null,
    outcome: '',
    cost: null,
    created_at: '2026-11-02T10:00:00',
    ...overrides,
  }
}

async function show(
  status: AssistantStatus,
  turns: AssistantTurn[] = [],
  personas: AssistantPersona[] = []
) {
  vi.spyOn(assistantApi, 'status').mockResolvedValue(status)
  vi.spyOn(assistantApi, 'personas').mockResolvedValue(personas)
  const sessions = turns.length
    ? [{ id: 's1', title: 'Tidy these', created_at: '', updated_at: '' }]
    : []
  vi.spyOn(assistantApi, 'listSessions').mockResolvedValue(sessions)
  vi.spyOn(assistantApi, 'getSession').mockResolvedValue({
    id: 's1',
    title: 'Tidy these',
    created_at: '',
    updated_at: '',
    messages: [],
    turns,
  })
  render(
    <MemoryRouter>
      <AssistantPanel />
    </MemoryRouter>
  )
  // Opening loads the status and the conversation; inside act so the panel has settled.
  await act(async () => {
    useAssistantStore.getState().setOpen(true)
  })
}

beforeEach(() => {
  vi.spyOn(configModule, 'loadConfig').mockResolvedValue({
    notes_base_url: 'https://notes.example.test',
    read_only: false,
    ai_backend: 'suite',
    suite_base_url: 'https://suite.example.test/',
  })
  useAssistantStore.getState().reset()
})

afterEach(() => {
  cleanup()
  useAssistantStore.getState().reset()
  vi.restoreAllMocks()
})

describe('AssistantPanel', () => {
  it('says so, and offers no text box, when the app is not on Gecko Suite', async () => {
    await show({
      available: false,
      ready: false,
      model: null,
      message: 'The assistant runs on Gecko Suite. Set AI_BACKEND=suite to turn it on.',
    })
    expect(await screen.findByText(/set AI_BACKEND=suite/i)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /message/i })).toBeDisabled()
  })

  it('says what is missing and links to the routing screen when no model is set up', async () => {
    await show({
      available: true,
      ready: false,
      model: null,
      message: 'No AI model is set up for the assistant.',
    })
    expect(await screen.findByText(/no ai model is set up/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open gecko suite/i })).toHaveAttribute(
      'href',
      'https://suite.example.test/routing'
    )
  })

  it('shows a proposal as what would change, and applies only the assets left ticked', async () => {
    const apply = vi.spyOn(assistantApi, 'apply').mockResolvedValue([])
    vi.spyOn(assistantApi, 'getTurn').mockResolvedValue(turn())
    await show(READY, [
      turn({
        outcome: 'pending',
        proposal: {
          items: [
            item('a1', 'IMG_001', { name: 'Harbour at dawn', add_tags: ['harbour'] }),
            item('a2', 'IMG_002', { name: 'Harbour at dusk' }),
          ],
        },
      }),
    ])
    const card = await screen.findByLabelText(/proposed changes/i)
    expect(within(card).getByText('Harbour at dawn')).toBeInTheDocument()
    // The old name is shown struck through beside the new one.
    expect(within(card).getAllByText('IMG_001').length).toBeGreaterThan(0)
    expect(within(card).getByText(/\+ harbour/)).toBeInTheDocument()

    await userEvent.click(
      within(card).getByRole('checkbox', { name: /include img_002/i })
    )
    await userEvent.click(within(card).getByRole('button', { name: /apply 1 of 2/i }))
    expect(apply).toHaveBeenCalledWith('t1', ['a1'])
  })

  it('discards without applying', async () => {
    const discard = vi
      .spyOn(assistantApi, 'discard')
      .mockResolvedValue(turn({ outcome: 'discarded' }))
    await show(READY, [
      turn({
        outcome: 'pending',
        proposal: { items: [item('a1', 'IMG_001', { name: 'New' })] },
      }),
    ])
    await userEvent.click(await screen.findByRole('button', { name: /discard/i }))
    expect(discard).toHaveBeenCalledWith('t1')
  })

  it('offers Stop, not Send, while a turn runs', async () => {
    await show(READY, [
      turn({ status: 'processing', reply: '', steps: [{ kind: 'search', text: 'x' }] }),
    ])
    expect(await screen.findByRole('button', { name: /stop/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^send$/i })).toBeNull()
  })

  it('sends on Enter and keeps the message on Shift+Enter', async () => {
    vi.spyOn(assistantApi, 'createSession').mockResolvedValue({
      id: 's1',
      title: '',
      created_at: '',
      updated_at: '',
    })
    const ask = vi
      .spyOn(assistantApi, 'ask')
      .mockResolvedValue(turn({ status: 'queued' }))
    vi.spyOn(assistantApi, 'getTurn').mockResolvedValue(turn({ status: 'queued' }))
    await show(READY)
    const box = await screen.findByRole('textbox', { name: /message/i })
    await waitFor(() => expect(box).toBeEnabled())
    await userEvent.type(box, 'find the harbour{Shift>}{Enter}{/Shift}more')
    expect(ask).not.toHaveBeenCalled()
    await userEvent.type(box, '{Enter}')
    await waitFor(() => expect(ask).toHaveBeenCalled())
    expect(ask.mock.calls[0][1]).toBe('find the harbour\nmore')
  })

  it('offers a persona picker only when the suite has personas, and asks as the one chosen', async () => {
    vi.spyOn(assistantApi, 'createSession').mockResolvedValue({
      id: 's1',
      title: '',
      created_at: '',
      updated_at: '',
    })
    const ask = vi
      .spyOn(assistantApi, 'ask')
      .mockResolvedValue(turn({ status: 'queued' }))
    vi.spyOn(assistantApi, 'getTurn').mockResolvedValue(turn({ status: 'queued' }))
    await show(
      READY,
      [],
      [{ id: 'p-arch', name: 'Archivist', description: 'Careful.', avatar: null }]
    )
    const picker = await screen.findByRole('combobox', { name: /persona/i })
    await userEvent.selectOptions(picker, 'p-arch')
    const box = await screen.findByRole('textbox', { name: /message/i })
    await waitFor(() => expect(box).toBeEnabled())
    await userEvent.type(box, 'Is this the original?{Enter}')
    await waitFor(() => expect(ask).toHaveBeenCalled())
    expect(ask.mock.calls[0][3]).toBe('p-arch')
  })

  it('has no picker when there are no personas', async () => {
    await show(READY)
    await screen.findByRole('textbox', { name: /message/i })
    expect(screen.queryByRole('combobox', { name: /persona/i })).toBeNull()
  })
})
