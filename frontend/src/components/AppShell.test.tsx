import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AppShell from '@/components/AppShell'
import { storageApi } from '@/api/storage'
import * as configApi from '@/api/config'
import { useActivityStore } from '@/stores/activity'
import { useAuthStore } from '@/stores/auth'

beforeEach(() => {
  useAuthStore.getState().reset()
  // The shell mounts ActivityIndicator, which starts a poll.
  vi.spyOn(useActivityStore.getState(), 'start').mockImplementation(() => {})
  // And StorageWarning, which checks the disk.
  vi.spyOn(storageApi, 'status').mockResolvedValue({
    level: 'ok',
    free_bytes: null,
    total_bytes: null,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

function mockConfig(readOnly: boolean) {
  vi.spyOn(configApi, 'loadConfig').mockResolvedValue({
    notes_base_url: 'https://notes.example.test',
    read_only: readOnly,
  })
}

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
      user: { id: 'u1', username: 'tester', is_admin: false },
      status: 'authenticated',
    })
    const signOut = vi.fn()
    useAuthStore.setState({ signOut })

    renderShell()
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }))

    expect(signOut).toHaveBeenCalled()
  })

  it('says when it is a read-only archive, and offers no sign-out there', async () => {
    mockConfig(true)
    useAuthStore.setState({
      user: { id: 'u1', username: 'tester', is_admin: false },
      status: 'authenticated',
    })

    renderShell()

    expect(await screen.findByText(/read-only archive copy/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sign out/i })).not.toBeInTheDocument()
  })

  it('shows no archive banner in the live app', async () => {
    mockConfig(false)
    renderShell()

    await screen.findByText('Content')
    expect(screen.queryByText(/read-only archive copy/i)).not.toBeInTheDocument()
  })
})
