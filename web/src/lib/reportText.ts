// How the prose Claude writes in a report is split up for display.

/** A report's text split into paragraphs at blank lines, each trimmed, empty ones left
 * out. */
export function paragraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
}
