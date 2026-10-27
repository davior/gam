/**
 * Which saved-page track each downloaded file is, for showing before upload.
 *
 * A mirror of `page_tracks.match_files` on the server, which is the one that counts: by
 * the site's id in the filename first (Pixabay names a download `artist-title-604094.mp3`),
 * then by title contained in the filename, longest title first, one file per track.
 */

interface Matchable {
  title: string
  match_id: string | null
}

function normalise(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function stem(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(0, dot) : filename
}

export function matchFiles<T extends Matchable>(
  tracks: T[],
  filenames: string[]
): Map<string, T | null> {
  const result = new Map<string, T | null>(filenames.map((name) => [name, null]))
  const taken = new Set<number>()
  const byId = new Map<string, number>()
  tracks.forEach((track, index) => {
    if (track.match_id) byId.set(track.match_id, index)
  })

  for (const name of filenames) {
    for (const number of stem(name).match(/\d+/g) ?? []) {
      const index = byId.get(number)
      if (index !== undefined && !taken.has(index)) {
        result.set(name, tracks[index])
        taken.add(index)
        break
      }
    }
  }

  const titled = tracks
    .map((track, index) => ({ index, title: normalise(track.title) }))
    .filter((entry) => entry.title)
    .sort((a, b) => b.title.length - a.title.length)
  for (const name of filenames) {
    if (result.get(name)) continue
    const padded = ` ${normalise(stem(name))} `
    for (const { index, title } of titled) {
      if (!taken.has(index) && padded.includes(` ${title} `)) {
        result.set(name, tracks[index])
        taken.add(index)
        break
      }
    }
  }
  return result
}
