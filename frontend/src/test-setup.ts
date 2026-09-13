import '@testing-library/jest-dom/vitest'

// jsdom implements no layout, so it has no scrollIntoView — every real browser does.
// Stubbed rather than guarded at the call site: adding an optional-call for a
// universally supported API would be defensive code for a test-environment gap.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {}
}
