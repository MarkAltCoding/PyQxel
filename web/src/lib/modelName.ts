// The name of the Claude model that wrote a report, as a person reads it: the API's
// "claude-opus-5-5" as "Claude Opus 5.5". A model named by Claude itself outside the app,
// such as "Claude Opus 5.5 (outside the app)", is shown as given.

export function modelName(model: string): string {
  const match = /^claude-([a-z]+)-(\d+(?:-\d+)*)$/.exec(model)
  if (!match)
    return model === 'claude (pasted)' ? 'Claude outside the app (model not given)' : model
  const [, family = '', version = ''] = match
  return `Claude ${family.charAt(0).toUpperCase()}${family.slice(1)} ${version.replace(/-/g, '.')}`
}
