import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Tabs, { type TabSpec } from '@/components/Tabs'

function tabs(): TabSpec[] {
  return [
    { id: 'details', label: 'Details', content: <p>the description</p> },
    { id: 'transcript', label: 'Transcript', content: <p>the transcript</p> },
    { id: 'info', label: 'Info', content: <p>the metadata</p> },
  ]
}

describe('Tabs', () => {
  it('opens on the first tab', () => {
    render(<Tabs tabs={tabs()} />)

    expect(screen.getByRole('tab', { name: 'Details' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByText('the description')).toBeVisible()
  })

  it('keeps the panels you are not looking at mounted', () => {
    /**
     * TranscriptPanel fetches on mount. Rendering only the active tab would refetch the
     * transcript — and throw away its scroll position — every time you checked the
     * description and came back.
     */
    render(<Tabs tabs={tabs()} />)

    expect(screen.getByText('the transcript')).toBeInTheDocument()
    expect(screen.getByText('the transcript')).not.toBeVisible()
    // Only the open one is exposed to assistive technology.
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
  })

  it('shows another panel when its tab is clicked', async () => {
    const user = userEvent.setup()
    render(<Tabs tabs={tabs()} />)

    await user.click(screen.getByRole('tab', { name: 'Transcript' }))

    expect(screen.getByText('the transcript')).toBeVisible()
    expect(screen.getByText('the description')).not.toBeVisible()
  })

  it('moves between tabs with the arrow keys, taking focus along', async () => {
    // A roving tabindex makes Tab reach only the selected tab, so without this the other
    // two would be unreachable from the keyboard.
    const user = userEvent.setup()
    render(<Tabs tabs={tabs()} />)

    await user.click(screen.getByRole('tab', { name: 'Details' }))
    await user.keyboard('{ArrowRight}')

    const transcript = screen.getByRole('tab', { name: 'Transcript' })
    expect(transcript).toHaveAttribute('aria-selected', 'true')
    expect(transcript).toHaveFocus()

    await user.keyboard('{End}')
    expect(screen.getByRole('tab', { name: 'Info' })).toHaveFocus()

    // Wraps rather than stopping at the end.
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Details' })).toHaveFocus()
  })

  it('falls back to the first tab when the open one goes away', async () => {
    /**
     * The asset panel swaps assets underneath this. Moving from a video to an image
     * retires the transcript tab, and the panel must not be left showing nothing.
     */
    const user = userEvent.setup()
    const { rerender } = render(<Tabs tabs={tabs()} />)

    await user.click(screen.getByRole('tab', { name: 'Transcript' }))
    expect(screen.getByText('the transcript')).toBeVisible()

    rerender(<Tabs tabs={tabs().filter((tab) => tab.id !== 'transcript')} />)

    expect(screen.getByRole('tab', { name: 'Details' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByText('the description')).toBeVisible()
  })
})
