import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DetailDock from '@/components/DetailDock'

/**
 * The dock asks one media query whether there is room to sit beside the library. The
 * stub in `test-setup.ts` always says no, so a test that wants the docked chrome has to
 * say so here.
 */
function roomToDock(available: boolean) {
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        media: query,
        matches: available,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList
  )
}

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.style.overflow = ''
})

describe('DetailDock', () => {
  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(
      <DetailDock label="Giordano interview" onClose={onClose}>
        <p>panel body</p>
      </DetailDock>
    )

    await user.keyboard('{Escape}')

    expect(onClose).toHaveBeenCalledOnce()
  })

  describe('with no room to dock', () => {
    it('covers the screen, and says it is modal while it does', () => {
      render(
        <DetailDock label="Giordano interview" onClose={vi.fn()}>
          <p>panel body</p>
        </DetailDock>
      )

      const panel = screen.getByRole('dialog', { name: 'Giordano interview' })
      expect(panel).toHaveAttribute('aria-modal', 'true')
      // Nothing to drag when the panel is the whole screen.
      expect(screen.queryByRole('separator')).not.toBeInTheDocument()
    })

    it('stops the library scrolling underneath, and lets it again on close', () => {
      const { unmount } = render(
        <DetailDock label="Giordano interview" onClose={vi.fn()}>
          <p>panel body</p>
        </DetailDock>
      )

      expect(document.body.style.overflow).toBe('hidden')

      unmount()

      expect(document.body.style.overflow).toBe('')
    })
  })

  describe('with room to dock', () => {
    beforeEach(() => {
      roomToDock(true)
    })

    it('sits beside the library rather than over it', () => {
      render(
        <DetailDock label="Giordano interview" onClose={vi.fn()}>
          <p>panel body</p>
        </DetailDock>
      )

      const panel = screen.getByRole('dialog', { name: 'Giordano interview' })
      // Docked it is not modal — the library beside it is still usable, and claiming
      // otherwise would tell a screen reader the rest of the page had gone away.
      expect(panel).not.toHaveAttribute('aria-modal')
      expect(panel).toHaveStyle({ width: '480px' })
      expect(document.body.style.overflow).toBe('')
    })

    it('resizes from the keyboard, not only by dragging', async () => {
      const user = userEvent.setup()
      render(
        <DetailDock label="Giordano interview" onClose={vi.fn()}>
          <p>panel body</p>
        </DetailDock>
      )

      const handle = screen.getByRole('separator', { name: /resize/i })
      expect(handle).toHaveAttribute('aria-valuenow', '480')

      handle.focus()
      // Left is "more panel" — the handle is on the panel's left edge.
      await user.keyboard('{ArrowLeft}')

      expect(handle).toHaveAttribute('aria-valuenow', '496')
    })

    it('remembers the width, and comes back to it', async () => {
      const user = userEvent.setup()
      const { unmount } = render(
        <DetailDock label="Giordano interview" onClose={vi.fn()}>
          <p>panel body</p>
        </DetailDock>
      )

      screen.getByRole('separator', { name: /resize/i }).focus()
      await user.keyboard('{ArrowRight}{ArrowRight}')
      unmount()

      render(
        <DetailDock label="Giordano interview" onClose={vi.fn()}>
          <p>panel body</p>
        </DetailDock>
      )

      expect(screen.getByRole('separator', { name: /resize/i })).toHaveAttribute(
        'aria-valuenow',
        '448'
      )
    })

    it('never lets the drag squeeze the library out entirely', async () => {
      const user = userEvent.setup()
      render(
        <DetailDock label="Giordano interview" onClose={vi.fn()}>
          <p>panel body</p>
        </DetailDock>
      )

      const handle = screen.getByRole('separator', { name: /resize/i })
      handle.focus()
      await user.keyboard('{End}')

      // 60% of jsdom's 1024px window, which is the ceiling here rather than the 56rem
      // absolute cap.
      expect(handle).toHaveAttribute('aria-valuenow', '614')
      expect(handle).toHaveAttribute('aria-valuemax', '614')

      await user.keyboard('{Home}')
      expect(handle).toHaveAttribute('aria-valuenow', '352')
    })
  })
})
