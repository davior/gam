import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StoragePanel from '@/components/StoragePanel'
import {
  storageApi,
  type ServerUsage,
  type StorageSummary,
  type Volume,
} from '@/api/storage'

const GB = 1024 ** 3

function volume(overrides: Partial<Volume> = {}): Volume {
  return {
    roles: ['media', 'database', 'working'],
    total_bytes: 2048 * GB,
    used_bytes: 1648 * GB,
    free_bytes: 400 * GB,
    level: 'ok',
    guarded: true,
    ...overrides,
  }
}

function server(overrides: Partial<ServerUsage> = {}): ServerUsage {
  return {
    volumes: [volume()],
    media: { files: 900, bytes: 1000 * GB },
    database_bytes: 2 * GB,
    stray: { files: 0, bytes: 0 },
    warn_free_bytes: 20 * GB,
    reserve_bytes: 5 * GB,
    ...overrides,
  }
}

function summary(overrides: Partial<StorageSummary> = {}): StorageSummary {
  return {
    level: 'ok',
    library: {
      total_bytes: 130 * GB,
      files: 40,
      by_type: [
        { asset_type: 'video', files: 34, bytes: 120 * GB },
        { asset_type: 'image', files: 6, bytes: 9 * GB },
      ],
      thumbnails: { files: 40, bytes: 1 * GB },
      largest: [
        { id: 'a1', name: 'Giordano interview', asset_type: 'video', size_bytes: 8 * GB },
        { id: 'a2', name: 'Harbour at dusk', asset_type: 'image', size_bytes: 2 * GB },
      ],
    },
    server: null,
    ...overrides,
  }
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <StoragePanel />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.spyOn(storageApi, 'summary').mockResolvedValue(summary())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('StoragePanel', () => {
  it('breaks the library down by type and links its largest files', async () => {
    renderPanel()

    expect(await screen.findByText('130.0 GB')).toBeInTheDocument()
    expect(screen.getByText('Video')).toBeInTheDocument()
    expect(screen.getByText('34 files · 120.0 GB')).toBeInTheDocument()
    expect(screen.getByText('Previews')).toBeInTheDocument()

    // The point of the list is to go and delete something, so each is a way there.
    const largest = screen.getByRole('link', { name: 'Giordano interview' })
    expect(largest).toHaveAttribute('href', '/a/a1')
    expect(screen.getByRole('link', { name: 'Harbour at dusk' })).toHaveAttribute(
      'href',
      '/a/a2'
    )
  })

  it('shows a non-admin no server numbers and no sweep', async () => {
    renderPanel()

    await screen.findByText('130.0 GB')
    expect(screen.queryByText(/free of/)).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.queryByRole('button', { name: /remove them/i })).toBeNull()
  })

  it('shows an admin the disk, split into what is GAM’s and what is not', async () => {
    vi.spyOn(storageApi, 'summary').mockResolvedValue(summary({ server: server() }))
    renderPanel()

    expect(await screen.findByText('Server disk')).toBeInTheDocument()
    expect(screen.getByText(/400\.0 GB free of 2\.0 TB/)).toBeInTheDocument()
    expect(
      screen.getByText('Holds media, the database and temporary files')
    ).toBeInTheDocument()

    const legend = screen.getByRole('list', { name: /what is on the server disk/i })
    expect(within(legend).getByText('GAM media')).toBeInTheDocument()
    expect(within(legend).getByText('1000.0 GB')).toBeInTheDocument()
    expect(within(legend).getByText('GAM database')).toBeInTheDocument()
    // 1648 used, of which 1002 is GAM's.
    expect(within(legend).getByText('Everything else')).toBeInTheDocument()
    expect(within(legend).getByText('646.0 GB')).toBeInTheDocument()
    // used + free == total here, so there is no reserve to show.
    expect(within(legend).queryByText(/reserved/i)).toBeNull()
  })

  it('names the filesystem reserve rather than calling it free', async () => {
    vi.spyOn(storageApi, 'summary').mockResolvedValue(
      summary({ server: server({ volumes: [volume({ used_bytes: 1548 * GB })] }) })
    )
    renderPanel()

    const legend = await screen.findByRole('list', {
      name: /what is on the server disk/i,
    })
    expect(within(legend).getByText('Reserved by the system')).toBeInTheDocument()
    expect(within(legend).getByText('100.0 GB')).toBeInTheDocument()
  })

  it('explains a separate temp disk that does not count', async () => {
    vi.spyOn(storageApi, 'summary').mockResolvedValue(
      summary({
        server: server({
          volumes: [
            volume({ roles: ['media', 'database'] }),
            volume({
              roles: ['working'],
              total_bytes: 8 * GB,
              used_bytes: 7 * GB,
              free_bytes: 1 * GB,
              level: 'critical',
              guarded: false,
            }),
          ],
        }),
      })
    )
    renderPanel()

    expect(await screen.findByText('Media disk')).toBeInTheDocument()
    expect(screen.getByText('Working space')).toBeInTheDocument()
    expect(screen.getByText(/does not count toward the warning/)).toBeInTheDocument()
    expect(screen.getByText(/a large import may not fit/)).toBeInTheDocument()
  })

  it('tells a non-admin why uploads stopped without the numbers', async () => {
    vi.spyOn(storageApi, 'summary').mockResolvedValue(summary({ level: 'critical' }))
    renderPanel()

    expect(await screen.findByText(/nearly full/)).toHaveTextContent('a safe margin')
  })

  it('gives an admin the thresholds', async () => {
    vi.spyOn(storageApi, 'summary').mockResolvedValue(
      summary({ level: 'low', server: server() })
    )
    renderPanel()

    expect(await screen.findByText(/Running low/)).toHaveTextContent('under 20.0 GB free')
  })

  it('says nothing about the level while there is room', async () => {
    renderPanel()

    await screen.findByText('130.0 GB')
    expect(screen.queryByText(/running low|nearly full/i)).toBeNull()
  })

  it('removes stray files only after asking', async () => {
    const strays = server({ stray: { files: 3, bytes: 2 * GB } })
    vi.spyOn(storageApi, 'summary').mockResolvedValue(summary({ server: strays }))
    const cleanup = vi
      .spyOn(storageApi, 'cleanup')
      .mockResolvedValue({ removed: { files: 3, bytes: 2 * GB }, skipped: 0 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPanel()

    const button = await screen.findByRole('button', { name: /remove them/i })
    expect(
      screen.getByText(/3 files \(2\.0 GB\) on disk that no asset refers to/)
    ).toBeInTheDocument()

    await userEvent.click(button)
    expect(confirm).toHaveBeenCalled()
    expect(cleanup).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    vi.mocked(storageApi.summary).mockResolvedValue(summary({ server: server() }))
    await userEvent.click(button)

    expect(cleanup).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Removed 3 files (2.0 GB).')).toBeInTheDocument()
    // Reloaded afterwards, so the count is the disk's and not a subtraction.
    expect(await screen.findByText('No stray files.')).toBeInTheDocument()
  })

  it('says when there is nothing in the library', async () => {
    vi.spyOn(storageApi, 'summary').mockResolvedValue(
      summary({
        library: {
          total_bytes: 0,
          files: 0,
          by_type: [],
          thumbnails: { files: 0, bytes: 0 },
          largest: [],
        },
      })
    )
    renderPanel()

    expect(await screen.findByText('Nothing stored yet.')).toBeInTheDocument()
  })

  it('reloads on demand', async () => {
    renderPanel()
    await screen.findByText('130.0 GB')

    await userEvent.click(screen.getByRole('button', { name: /refresh storage/i }))

    await waitFor(() => expect(storageApi.summary).toHaveBeenCalledTimes(2))
  })

  it('shows the error when it cannot load', async () => {
    vi.spyOn(storageApi, 'summary').mockRejectedValue(
      new Error('Could not reach the server')
    )
    renderPanel()

    expect(await screen.findByText('Could not reach the server')).toBeInTheDocument()
  })
})
