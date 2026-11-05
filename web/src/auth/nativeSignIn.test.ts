import { afterEach, describe, expect, it } from 'vitest'
import {
  forgetNativeSignIn,
  newVerifier,
  pendingVerifier,
  signInLinkFragment,
} from './nativeSignIn'

afterEach(() => window.localStorage.clear())

describe('native sign-in', () => {
  it('makes a verifier and its S256 challenge as the server checks them', async () => {
    const { verifier, challenge } = await newVerifier()
    expect(verifier).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/)
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
    const expected = btoa(String.fromCharCode(...new Uint8Array(digest)))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
    expect(challenge).toBe(expected)
  })

  it('reads only sign-in links', () => {
    expect(signInLinkFragment('pyqxel://signin#code=abc&next=%2F')).toBe('code=abc&next=%2F')
    expect(signInLinkFragment('pyqxel://signin/#error=x')).toBe('error=x')
    expect(signInLinkFragment('pyqxel://signinelsewhere#code=abc')).toBeNull()
    expect(signInLinkFragment('pyqxel://other#code=abc')).toBeNull()
    expect(signInLinkFragment('https://example.com/signin#code=abc')).toBeNull()
  })

  it('trusts a waiting sign-in for 15 minutes, until it is used up', () => {
    const now = Date.now()
    window.localStorage.setItem('pyqxel.appSignIn', JSON.stringify({ verifier: 'v', started: now }))
    expect(pendingVerifier(now + 60_000)).toBe('v')
    expect(pendingVerifier(now + 16 * 60_000)).toBeNull()
    forgetNativeSignIn()
    expect(pendingVerifier(now)).toBeNull()
  })
})
