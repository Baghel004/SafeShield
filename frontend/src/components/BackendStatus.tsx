import { useEffect, useState } from 'react'

import { isBackendUp } from '../lib/api'

type Status = 'checking' | 'up' | 'down'

/**
 * A banner shown only when the backend cannot be reached.
 *
 * This is a deliberate demo affordance. The API runs on an on-demand cluster
 * that is torn down between sessions, and a visitor who lands on a permanently
 * hosted frontend with no backend behind it should be told that plainly rather
 * than left clicking a login button that fails for reasons they cannot see.
 */
export function BackendStatus() {
  const [status, setStatus] = useState<Status>('checking')

  useEffect(() => {
    let cancelled = false

    const check = async () => {
      const up = await isBackendUp()
      if (!cancelled) setStatus(up ? 'up' : 'down')
    }

    void check()
    // Re-check while it is down, so the banner clears on its own once the
    // backend comes up mid-session rather than needing a reload.
    const timer = setInterval(check, 15000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  if (status !== 'down') return null

  return (
    <div
      role="status"
      className="bg-amber-500/15 text-amber-800 dark:text-amber-200 px-4 py-2 text-center text-sm"
    >
      The demo backend is currently offline. It runs on-demand to keep hosting
      costs down — check back shortly, or view the{' '}
      <a
        href="https://github.com/Baghel004/SafeShield"
        className="underline underline-offset-2"
        target="_blank"
        rel="noreferrer"
      >
        source on GitHub
      </a>
      .
    </div>
  )
}
