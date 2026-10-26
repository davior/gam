import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import type React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MusicPanel from '@/components/MusicPanel'
import { enrichmentApi } from '@/api/enrichment'
import { splitTerms } from '@/utils/terms'
import { useLibraryStore } from '@/stores/library'
import type { Asset } from '@/api/assets'
import { noAttribution } from '@/test-fixtures'

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'a1',
    name: 'Caravan theme',
    description: null,
    summary: null,
    ...noAttribution,
    asset_type: 'audio',
    source: 'local_upload',
    parent_asset_id: null,
    in_point: null,
    out_point: null,
    original_name: 'caravan.mp3',
    mime_type: 'audio/mpeg',
    file_format: 'mp3',
    size_bytes: 100,
    duration_seconds: 180,
    width: null,
    height: null,
    codec: 'mp3',
    file_url: null,
    thumb_url: null,
    missing: false,
    tags: [],
    upload_date: '2026-01-01T00:00:00',
    modified_date: '2026-01-01T00:00:00',
    metadata_modified_date: '2026-01-01T00:00:00',
    ...overrides,
  }
}

const update = vi.fn().mockResolvedValue(undefined)

// The measure button renders a Link (to Settings, on a provider error), so needs a router.
function renderPanel(panel: React.ReactElement) {
  return render(<MemoryRouter>{panel}</MemoryRouter>)
}

beforeEach(() => {
  update.mockClear()
  useLibraryStore.setState({ update })
})

describe('splitTerms', () => {
  it('splits on the separators tag writers use', () => {
    expect(splitTerms('Ambient; Cinematic, Hip-Hop/Rap')).toEqual([
      'Ambient',
      'Cinematic',
      'Hip-Hop',
      'Rap',
    ])
    expect(splitTerms('  ,  ')).toEqual([])
  })
})

describe('MusicPanel', () => {
  it('shows what the file tags already said', () => {
    renderPanel(
      <MusicPanel
        asset={asset({
          music_genres: ['Ambient', 'Cinematic'],
          music_bpm: 92,
          music_key: 'Am',
          music_vocals: false,
        })}
      />
    )

    expect(screen.getByLabelText('Genres')).toHaveValue('Ambient, Cinematic')
    expect(screen.getByLabelText('Tempo (BPM)')).toHaveValue('92')
    expect(screen.getByLabelText('Key')).toHaveValue('Am')
    expect(screen.getByLabelText('Vocals')).toHaveValue('instrumental')
  })

  it('sends only the fields that changed', async () => {
    // A BPM read from the file must keep its "embedded" stamp until someone edits it.
    const user = userEvent.setup()
    renderPanel(<MusicPanel asset={asset({ music_bpm: 92 })} />)

    await user.type(screen.getByLabelText('Moods'), 'Tense, dark')
    await user.selectOptions(screen.getByLabelText('Energy'), 'low')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(update).toHaveBeenCalledWith('a1', {
      music_moods: ['Tense', 'dark'],
      music_energy: 'low',
    })
  })

  it('clears a field back to not known', async () => {
    const user = userEvent.setup()
    renderPanel(
      <MusicPanel asset={asset({ music_vocals: true, music_genres: ['Folk'] })} />
    )

    await user.selectOptions(screen.getByLabelText('Vocals'), '')
    await user.clear(screen.getByLabelText('Genres'))
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(update).toHaveBeenCalledWith('a1', { music_genres: [], music_vocals: null })
  })

  it('measures the track from its sound on request', async () => {
    const start = vi.spyOn(enrichmentApi, 'analyzeMusic').mockResolvedValue({
      id: 'j1',
      kind: 'enrichment',
      action: 'analyze_music',
      status: 'queued',
      stalled: false,
      stage: 'Queued',
      progress: 0,
      detail: '',
      asset_id: 'a1',
      asset_name: 'Caravan theme',
      model: '',
      result_asset_id: null,
      result_asset_ids: [],
      error_message: null,
      created_at: '2026-10-26T00:00:00Z',
      updated_at: '2026-10-26T00:00:00Z',
    })
    const user = userEvent.setup()
    renderPanel(<MusicPanel asset={asset()} />)

    await user.click(
      screen.getByRole('button', { name: /Measure tempo, key and energy/ })
    )
    expect(start).toHaveBeenCalledWith('a1')
  })

  it('offers no measuring on a clip, which has no sound of its own', () => {
    renderPanel(<MusicPanel asset={asset({ parent_asset_id: 'p1' })} />)
    expect(screen.queryByRole('button', { name: /Measure/ })).not.toBeInTheDocument()
  })

  it('will not save a tempo that is not one', async () => {
    const user = userEvent.setup()
    renderPanel(<MusicPanel asset={asset()} />)

    await user.type(screen.getByLabelText('Tempo (BPM)'), '2019')

    expect(screen.getByText('A tempo between 20 and 300.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})
