import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AppShell from '@/components/AppShell'
import { useActivityStore } from '@/stores/activity'
import { useAuthStore } from '@/stores/auth'

beforeEach(() => {
  useAuthStore.getState().reset()
  // The shell mounts ActivityIndicator, which starts a poll.
  vi.spyOn(useActivityStore.getState(), 'start').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

function renderShell() {
  return render(
    <MemoryRouter>
      <AppShell>
        <p>Content</p>
      </AppShell>
    </MemoryRouter>
  )
}

describe('AppShell', () => {
  it('offers no sign-out to a visitor who is not signed in', () => {
    renderShell()
    expect(screen.queryByRole('button', { name: /sign out/i })).not.toBeInTheDocument()
  })

  it('signs the user out', async () => {
    // `signOut` has existed since M2 and was called by nothing — there was no way out
    // of a session short of clearing localStorage by hand.
    useAuthStore.setState({
      user: { id: 'u1', username: 'tester' },
      status: 'authenticated',
    })
    const signOut = vi.fn()
    useAuthStore.setState({ signOut })

    renderShell()
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }))

    expect(signOut).toHaveBeenCalled()
  })
})
