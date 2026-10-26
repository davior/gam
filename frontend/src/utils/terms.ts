/** Separators a person or a tag writer uses between terms. */
export function splitTerms(value: string): string[] {
  return value
    .split(/[;,/]/)
    .map((term) => term.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
}
