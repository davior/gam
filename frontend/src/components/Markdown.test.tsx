import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import Markdown from '@/components/Markdown'

function show(text: string) {
  return render(
    <MemoryRouter>
      <Markdown text={text} />
    </MemoryRouter>
  )
}

describe('Markdown', () => {
  it('draws bold, lists and code instead of their marks', () => {
    const { container } = show(
      'A **bold** point.\n\n- one\n- two\n\n1. first\n2. second\n\nUse `ffmpeg` here.'
    )
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(container.querySelectorAll('ul > li')).toHaveLength(2)
    expect(container.querySelectorAll('ol > li')).toHaveLength(2)
    expect(screen.getByText('ffmpeg').tagName).toBe('CODE')
    expect(container.textContent).not.toMatch(/\*\*|`/)
  })

  it('turns headings into bold lines, not page headings', () => {
    const { container } = show('## Summary\n\nText')
    expect(screen.getByText('Summary')).toBeInTheDocument()
    expect(container.querySelector('h1,h2,h3')).toBeNull()
  })

  it('draws a table', () => {
    const { container } = show('| a | b |\n| - | - |\n| 1 | 2 |')
    expect(container.querySelectorAll('th')).toHaveLength(2)
    expect(container.querySelectorAll('td')).toHaveLength(2)
  })

  it('opens outside links in a new tab and keeps in-app paths in the app', () => {
    show('[out](https://example.com) and [in](/library)')
    const out = screen.getByRole('link', { name: 'out' })
    expect(out).toHaveAttribute('target', '_blank')
    expect(out).toHaveAttribute('rel', expect.stringContaining('noopener'))
    expect(screen.getByRole('link', { name: 'in' })).not.toHaveAttribute('target')
  })

  it('never renders raw HTML or a script link from a reply', () => {
    const { container } = show('<img src=x onerror=alert(1)> [x](javascript:alert(1))')
    expect(container.querySelector('img')).toBeNull()
    const link = container.querySelector('a')
    expect(link?.getAttribute('href') ?? '').not.toMatch(/javascript:/i)
  })
})
