import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import StorageWarning from '@/components/StorageWarning'
import { storageApi, type StorageStatus } from '@/api/storage'

const GB = 1024 ** 3

function renderWarning(status: StorageStatus | Error) {
  if (status instanceof Error) vi.spyOn(storageApi, 'status').mockRejectedValue(status)
  else vi.spyOn(storageApi, 'status').mockResolvedValue(status)
  return render(
    <MemoryRouter>
      <StorageWarning />
    </MemoryRouter>
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('StorageWarning', () => {
  it('shows nothing while there is room', async () => {
    renderWarning({ level: 'ok', free_bytes: 400 * GB, total_bytes: 2048 * GB })

    await vi.waitFor(() => expect(storageApi.status).toHaveBeenCalled())
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('warns when space is low and links to the panel', async () => {
    renderWarning({ level: 'low', free_bytes: null, total_bytes: null })

    const link = await screen.findByRole('link', { name: 'Disk space low' })
    expect(link).toHaveAttribute('href', '/settings#storage')
    expect(link.className).toContain('text-amber-600')
  })

  it('tells an admin how much is left when it is nearly full', async () => {
    renderWarning({ level: 'critical', free_bytes: 3 * GB, total_bytes: 2048 * GB })

    const link = await screen.findByRole('link', {
      name: /disk nearly full — 3\.0 GB free/i,
    })
    expect(link.className).toContain('text-red-600')
  })

  it('shows nothing when the check fails', async () => {
    renderWarning(new Error('Could not reach the server'))

    await vi.waitFor(() => expect(storageApi.status).toHaveBeenCalled())
    expect(screen.queryByRole('link')).toBeNull()
  })
})
