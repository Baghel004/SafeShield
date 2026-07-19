import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { auth, setSessionLostHandler } from '../lib/api'
import type { User } from '../lib/types'

interface AuthState {
  user: User | null
  /** True until the initial session restore finishes. */
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName?: string) => Promise<void>
  demo: () => Promise<void>
  logout: () => Promise<void>
}

// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  // On load there is no access token -- it only ever lived in memory. The
  // refresh cookie is the sole evidence a session exists, so the app asks for
  // a new access token before deciding whether to show the login screen.
  // Without this every reload looks like a logout.
  useEffect(() => {
    let cancelled = false

    void (async () => {
      const restored = await auth.restore()
      if (cancelled) return
      if (restored) {
        try {
          setUser(await auth.me())
        } catch {
          setUser(null)
        }
      }
      if (!cancelled) setLoading(false)
    })()

    return () => {
      cancelled = true
    }
  }, [])

  // When a refresh fails mid-session, clear the user so the guard redirects.
  useEffect(() => {
    setSessionLostHandler(() => setUser(null))
    return () => setSessionLostHandler(null)
  }, [])

  const finish = useCallback(async () => {
    setUser(await auth.me())
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      login: async (email, password) => {
        await auth.login(email, password)
        await finish()
      },
      register: async (email, password, fullName) => {
        await auth.register(email, password, fullName)
        await finish()
      },
      demo: async () => {
        await auth.demo()
        await finish()
      },
      logout: async () => {
        await auth.logout()
        setUser(null)
      },
    }),
    [user, loading, finish],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
