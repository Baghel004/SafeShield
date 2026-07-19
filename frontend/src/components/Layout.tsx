import type { ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { Button } from './ui'

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="flex h-full flex-col">
      <header className="border-ink-200 dark:border-ink-800 dark:bg-ink-950/80 sticky top-0 z-10 border-b bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center gap-4 px-4 py-3">
          <span className="font-semibold tracking-tight">SafeShield</span>

          <nav className="flex gap-1">
            {[
              { to: '/chat', label: 'Ask' },
              { to: '/documents', label: 'Documents' },
            ].map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm transition-colors ${
                    isActive
                      ? 'bg-ink-100 dark:bg-ink-800 font-medium'
                      : 'text-ink-600 dark:text-ink-400 hover:text-ink-900 dark:hover:text-ink-100'
                  }`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {user?.is_demo && (
              <span className="bg-brand-500/10 text-brand-600 dark:text-brand-400 rounded-full px-2 py-0.5 text-xs font-medium">
                Demo
              </span>
            )}
            <span className="text-ink-400 hidden text-xs sm:inline">{user?.email}</span>
            <Button
              variant="ghost"
              className="px-2 py-1 text-xs"
              onClick={async () => {
                await logout()
                navigate('/login', { replace: true })
              }}
            >
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <main className="min-h-0 flex-1">{children}</main>
    </div>
  )
}
