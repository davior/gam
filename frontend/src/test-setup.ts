import '@testing-library/jest-dom/vitest'

// jsdom implements no layout, so it has no scrollIntoView — every real browser does.
// Stubbed rather than guarded at the call site: adding an optional-call for a
// universally supported API would be defensive code for a test-environment gap.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {}
}

// jsdom ships no IntersectionObserver, and the library grid uses one to page as you
// scroll. A no-op stub rather than a guard at the call site: the alternative is an
// optional-chained observer in production code to accommodate a test environment.
if (!('IntersectionObserver' in globalThis)) {
  class NoopIntersectionObserver implements IntersectionObserver {
    readonly root = null
    readonly rootMargin = ''
    readonly thresholds: ReadonlyArray<number> = []
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords(): IntersectionObserverEntry[] {
      return []
    }
  }
  globalThis.IntersectionObserver =
    NoopIntersectionObserver as unknown as typeof IntersectionObserver
}
