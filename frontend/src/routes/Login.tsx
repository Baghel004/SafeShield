import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { Alert, Button, Input } from '../components/ui'
import { ApiError } from '../lib/api'

type Mode = 'login' | 'register'

export function Login() {
  const { user, loading, login, register, demo } = useAuth()
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'form' | 'demo' | null>(null)

  if (loading) return null
  if (user) return <Navigate to="/chat" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy('form')
    try {
      if (mode === 'login') await login(email, password)
      else await register(email, password, fullName)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.')
    } finally {
      setBusy(null)
    }
  }

  async function onDemo() {
    setError(null)
    setBusy('demo')
    try {
      await demo()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Demo sign-in is unavailable.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">SafeShield</h1>
          <p className="text-ink-600 dark:text-ink-400 mt-2 text-sm">
            Ask questions about insurance policies. Every answer is grounded in the policy text and
            cites where it came from.
          </p>
        </div>

        <div className="border-ink-200 dark:border-ink-800 dark:bg-ink-900 rounded-2xl border bg-white p-6 shadow-sm">
          <Button
            variant="primary"
            className="w-full"
            onClick={onDemo}
            disabled={busy !== null}
            type="button"
          >
            {busy === 'demo' ? 'Signing in…' : 'Try the demo'}
          </Button>
          <p className="text-ink-400 mt-2 text-center text-xs">
            No sign-up. Opens a shared account with the sample policies loaded.
          </p>

          <div className="my-5 flex items-center gap-3">
            <span className="bg-ink-200 dark:bg-ink-800 h-px flex-1" />
            <span className="text-ink-400 text-xs">or</span>
            <span className="bg-ink-200 dark:bg-ink-800 h-px flex-1" />
          </div>

          <form onSubmit={onSubmit} className="space-y-4">
            {mode === 'register' && (
              <Input
                label="Name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                autoComplete="name"
              />
            )}
            <Input
              label="Email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
            <Input
              label="Password"
              type="password"
              required
              minLength={mode === 'register' ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
            />

            {error && <Alert>{error}</Alert>}

            <Button type="submit" className="w-full" disabled={busy !== null}>
              {busy === 'form' ? 'Working…' : mode === 'login' ? 'Sign in' : 'Create account'}
            </Button>
          </form>

          <button
            type="button"
            className="text-ink-600 dark:text-ink-400 hover:text-brand-500 mt-4 w-full text-center text-sm"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setError(null)
            }}
          >
            {mode === 'login' ? 'Need an account? Sign up' : 'Already have an account? Sign in'}
          </button>
        </div>

        <p className="text-ink-400 mt-6 text-center text-xs">
          Demo project. Uploaded documents are processed by a third-party LLM API. Not financial or
          legal advice.
        </p>
      </div>
    </div>
  )
}
