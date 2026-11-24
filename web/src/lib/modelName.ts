// The name of the Claude model that wrote a report, as a person reads it: the API's
// "claude-opus-5-5" as "Claude Opus 5.5". A model named by Claude itself outside the app,
// such as "Claude Opus 5.5 (outside the app)", is shown as given.

// With the effort Claude was asked to put in, it follows the name: "Claude Opus 5.5 at
// high effort". A report written outside the app has no effort, and none is shown.

export function modelName(model: string, effort?: string | null): string {
  const name = nameOf(model)
  if (!effort) return name
  const at = ` at ${EFFORT_NAMES[effort] ?? effort} effort`
  // "Claude Opus 5.5 at high effort (outside the app)", with the mark kept last.
  return name.endsWith(OUTSIDE)
    ? `${name.slice(0, -OUTSIDE.length)}${at}${OUTSIDE}`
    : `${name}${at}`
}

/** The mark the server gives a model named for an answer written outside the app. */
const OUTSIDE = ' (outside the app)'

/** The effort settings in words; "xhigh" is the API's name for the step above high. */
const EFFORT_NAMES: Record<string, string> = {
  low: 'low',
  medium: 'medium',
  high: 'high',
  xhigh: 'extra-high',
  max: 'maximum',
}

function nameOf(model: string): string {
  const match = /^claude-([a-z]+)-(\d+(?:-\d+)*)$/.exec(model)
  if (!match)
    return model === 'claude (pasted)' ? 'Claude outside the app (model not given)' : model
  const [, family = '', version = ''] = match
  return `Claude ${family.charAt(0).toUpperCase()}${family.slice(1)} ${version.replace(/-/g, '.')}`
}
