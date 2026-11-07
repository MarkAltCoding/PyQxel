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
      Having Claude write {what} here needs an Anthropic key, which you can add in{' '}
      <Link to="/settings#provider-keys">Settings</Link>; Anthropic bills each request to it.
      Without one, choose <strong>Use Claude outside the app</strong> below: it gives you the same
      prompt to paste into Claude on claude.ai, and takes the answer back.
    </Notice>
  )
}
