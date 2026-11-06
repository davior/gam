import { create } from 'zustand'
import {
  assistantApi,
  isActiveTurn,
  type ApplyResult,
  type ApplySelection,
  type AssistantPersona,
  type AssistantSession,
  type AssistantSessionDetail,
  type AssistantStatus,
  type AssistantTurn,
} from '@/api/assistant'
import { apiErrorMessage } from '@/api/client'
import { useLibraryStore } from '@/stores/library'
import { useTagStore } from '@/stores/tags'

/**
 * The assistant panel's state: whether it is open, the saved conversations, the one on
 * screen, and the running turn.
 *
 * Lives in a store and not in the panel because App.tsx wraps each route in its own
 * AppShell: the panel remounts on every navigation, and a conversation held in component
 * state would be gone each time someone clicked from the library to an asset.
 *
 * A turn runs on the server whether or not anyone is looking, so the poll is only about
 * showing it. Closing the panel stops it; opening it asks again and finds the turn
 * running, or finished and waiting for Apply.
 */

const POLL_MS = 1500

interface AssistantState {
  open: boolean
  status: AssistantStatus | null
  sessions: AssistantSession[]
  /** The suite's personas the person may ask as, for the picker. */
  personas: AssistantPersona[]
  /** The one chosen for the next message; null is the assistant as it is. */
  personaId: string | null
  active: AssistantSessionDetail | null
  /** The asset on screen elsewhere, so "this one" in a message means something. */
  focus: { id: string; name: string } | null
  sending: boolean
  error: string | null
  /** What the last Apply did, per asset, until the next message. */
  applied: ApplyResult[] | null

  setOpen: (open: boolean) => void
  setFocus: (focus: { id: string; name: string } | null) => void
  setPersona: (id: string | null) => void
  load: () => Promise<void>
  select: (id: string) => Promise<void>
  newChat: () => void
  send: (prompt: string) => Promise<void>
  stop: () => Promise<void>
  apply: (turnId: string, selection?: ApplySelection) => Promise<void>
  discard: (turnId: string) => Promise<void>
  remove: (id: string) => Promise<void>
  reset: () => void
}

let timer: ReturnType<typeof setTimeout> | null = null
// Bumped by every reset, selection and close, so a response that lands after one is
// dropped instead of putting an old conversation back on screen.
let epoch = 0

function stopPolling() {
  if (timer) clearTimeout(timer)
  timer = null
}

export const useAssistantStore = create<AssistantState>((set, get) => {
  /** Replace one turn inside the open conversation. */
  function putTurn(turn: AssistantTurn) {
    const active = get().active
    if (!active || active.id !== turn.session_id) return
    const known = active.turns.some((t) => t.id === turn.id)
    set({
      active: {
        ...active,
        turns: known
          ? active.turns.map((t) => (t.id === turn.id ? turn : t))
          : [...active.turns, turn],
      },
    })
  }

  /** Keep asking about a running turn; when it ends, take the saved conversation. */
  function watch(turnId: string, mine: number) {
    stopPolling()
    timer = setTimeout(async () => {
      try {
        const turn = await assistantApi.getTurn(turnId)
        if (mine !== epoch) return
        putTurn(turn)
        if (isActiveTurn(turn)) return watch(turnId, mine)
        // Finished: the conversation now has the assistant's message and a new title.
        const detail = await assistantApi.getSession(turn.session_id)
        if (mine !== epoch) return
        set({ active: detail })
        void get().load()
      } catch (error) {
        if (mine !== epoch) return
        set({ error: apiErrorMessage(error, 'Lost track of the assistant') })
      }
    }, POLL_MS)
  }

  function resume(detail: AssistantSessionDetail, mine: number) {
    const running = detail.turns.find(isActiveTurn)
    if (running) watch(running.id, mine)
  }

  return {
    open: false,
    status: null,
    sessions: [],
    personas: [],
    personaId: null,
    active: null,
    focus: null,
    sending: false,
    error: null,
    applied: null,

    setOpen(open) {
      set({ open })
      if (!open) {
        epoch += 1
        stopPolling()
        return
      }
      void get().load()
    },

    setFocus(focus) {
      set({ focus })
    },

    setPersona(id) {
      set({ personaId: id })
    },

    async load() {
      const mine = epoch
      try {
        const [status, sessions, personas] = await Promise.all([
          assistantApi.status(),
          assistantApi.listSessions(),
          // A picker is a nicety: whatever goes wrong fetching one is no reason to fail
          // the panel, which just has none.
          assistantApi.personas().catch(() => [] as AssistantPersona[]),
        ])
        if (mine !== epoch) return
        // A persona deleted in the suite since it was chosen must not be sent again.
        const personaId = personas.some((p) => p.id === get().personaId)
          ? get().personaId
          : null
        set({ status, sessions, personas, personaId, error: null })
        // Reopening the panel lands on the conversation that was on screen, or the
        // latest one, rather than an empty box.
        const current = get().active
        if (!current && sessions.length > 0) await get().select(sessions[0].id)
        else if (current) {
          const detail = await assistantApi.getSession(current.id).catch(() => null)
          if (mine !== epoch) return
          if (detail) {
            set({ active: detail })
            resume(detail, mine)
          }
        }
      } catch (error) {
        if (mine !== epoch) return
        set({ error: apiErrorMessage(error, 'Could not reach the assistant') })
      }
    },

    async select(id) {
      epoch += 1
      const mine = epoch
      stopPolling()
      set({ applied: null, error: null })
      try {
        const detail = await assistantApi.getSession(id)
        if (mine !== epoch) return
        set({ active: detail })
        resume(detail, mine)
      } catch (error) {
        if (mine !== epoch) return
        set({ error: apiErrorMessage(error, 'Could not open that conversation') })
      }
    },

    newChat() {
      epoch += 1
      stopPolling()
      // Created on the first message, so an unused "New chat" leaves nothing behind.
      set({ active: null, applied: null, error: null })
    },

    async send(prompt) {
      const text = prompt.trim()
      if (!text || get().sending) return
      const mine = epoch
      set({ sending: true, error: null, applied: null })
      try {
        let session = get().active
        if (!session) {
          const created = await assistantApi.createSession()
          if (mine !== epoch) return
          session = { ...created, messages: [], turns: [] }
          set({ active: session })
        }
        const turn = await assistantApi.ask(
          session.id,
          text,
          get().focus?.id,
          get().personaId
        )
        if (mine !== epoch) return
        const active = get().active
        if (active) {
          set({
            active: {
              ...active,
              messages: [
                ...active.messages,
                { role: 'user', text, turn_id: turn.id, at: turn.created_at },
              ],
            },
          })
        }
        putTurn(turn)
        watch(turn.id, mine)
        void get().load()
      } catch (error) {
        if (mine !== epoch) return
        set({ error: apiErrorMessage(error, 'The assistant could not start') })
      } finally {
        if (mine === epoch) set({ sending: false })
      }
    },

    async stop() {
      const turn = get().active?.turns.find(isActiveTurn)
      if (!turn) return
      try {
        await assistantApi.cancel(turn.id)
      } catch (error) {
        set({ error: apiErrorMessage(error, 'Could not stop that') })
      }
    },

    async apply(turnId, selection) {
      const mine = epoch
      try {
        const results = await assistantApi.apply(turnId, selection)
        if (mine !== epoch) return
        set({ applied: results, error: null })
        const turn = await assistantApi.getTurn(turnId)
        if (mine !== epoch) return
        putTurn(turn)
        // The library on screen and the tag vocabulary are now behind what was written.
        void useLibraryStore.getState().load()
        useTagStore.getState().reset()
      } catch (error) {
        if (mine !== epoch) return
        set({ error: apiErrorMessage(error, 'Could not apply that') })
      }
    },

    async discard(turnId) {
      const mine = epoch
      try {
        const turn = await assistantApi.discard(turnId)
        if (mine !== epoch) return
        putTurn(turn)
      } catch (error) {
        if (mine !== epoch) return
        set({ error: apiErrorMessage(error, 'Could not discard that') })
      }
    },

    async remove(id) {
      try {
        await assistantApi.deleteSession(id)
      } catch (error) {
        set({ error: apiErrorMessage(error, 'Could not delete that conversation') })
        return
      }
      if (get().active?.id === id) get().newChat()
      void get().load()
    },

    reset() {
      epoch += 1
      stopPolling()
      set({
        open: false,
        status: null,
        sessions: [],
        personas: [],
        personaId: null,
        active: null,
        focus: null,
        sending: false,
        error: null,
        applied: null,
      })
    },
  }
})
