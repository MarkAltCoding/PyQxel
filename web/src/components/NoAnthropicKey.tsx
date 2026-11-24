import { Link } from 'react-router'
import { Notice } from './Notice'

/**
 * Says, where Claude would write something, that it needs an Anthropic key this browser
 * does not have, and points to the free way round it: copying the prompt into Claude
 * outside the app, which every AI feature offers below its button.
 */
export function NoAnthropicKey({ what }: { what: string }) {
  return (
    <Notice>
      Writing {what} here needs an Anthropic key, added in{' '}
      <Link to="/settings#provider-keys">Settings</Link>. Without one, use{' '}
      <strong>Use Claude outside the app</strong> below to paste the same prompt into claude.ai.
    </Notice>
  )
}
