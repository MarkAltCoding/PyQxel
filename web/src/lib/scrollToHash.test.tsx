import { render, act } from '@testing-library/react'
import { useState } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useScrollToHash } from './scrollToHash'

/** A page whose section appears only once its "data" has loaded. */
function Page({ later }: { later: boolean }) {
  useScrollToHash()
  const [loaded, setLoaded] = useState(!later)
  return (
    <>
      <button type="button" onClick={() => setLoaded(true)}>
        load
      </button>
      {loaded && <section id="news">News</section>}
    </>
  )
}

function renderAt(path: string, later = false) {
  const router = createMemoryRouter([{ path: '/page', element: <Page later={later} /> }], {
    initialEntries: [path],
  })
  render(<RouterProvider router={router} />)
  return router
}

/** Records what was scrolled to; jsdom has no scrollIntoView of its own. */
const scrolled: Element[] = []

beforeEach(() => {
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolled.push(this)
  }
})

afterEach(() => {
  scrolled.length = 0
  delete (Element.prototype as Partial<Element>).scrollIntoView
})

describe('useScrollToHash', () => {
  it('scrolls to the section the link names', () => {
    renderAt('/page#news')

    expect(scrolled.map((element) => element.id)).toEqual(['news'])
  })

  it('waits for a section that renders after its data loads', async () => {
    renderAt('/page#news', true)
    expect(scrolled).toEqual([])

    await act(async () => document.querySelector('button')!.click())

    expect(scrolled.map((element) => element.id)).toEqual(['news'])
  })

  it('does nothing without a fragment, or with a malformed one', () => {
    renderAt('/page')
    renderAt('/page#%E0%A4%A')

    expect(scrolled).toEqual([])
  })
})
