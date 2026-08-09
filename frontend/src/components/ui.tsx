import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

export function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }) {
  const styles = {
    primary: 'bg-brand-600 text-white hover:bg-brand-500 disabled:bg-ink-400',
    ghost: 'bg-transparent text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800',
    danger: 'bg-transparent text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40',
  }[variant]

  return (
    <button
      className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${styles} ${className}`}
      {...props}
    />
  )
}

export function Input({
  label,
  error,
  className = '',
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string }) {
  return (
    <label className="block">
      <span className="text-ink-600 dark:text-ink-200 mb-1.5 block text-sm font-medium">
        {label}
      </span>
      <input
        className={`border-ink-200 dark:border-ink-800 dark:bg-ink-900 focus:border-brand-500 focus:ring-brand-500/30 w-full rounded-lg border px-3 py-2 text-sm outline-none focus:ring-2 ${className}`}
        aria-invalid={error ? true : undefined}
        {...props}
      />
      {error && <span className="mt-1 block text-sm text-red-600">{error}</span>}
    </label>
  )
}

export function Alert({ children, tone = 'error' }: { children: ReactNode; tone?: 'error' | 'info' }) {
  const styles =
    tone === 'error'
      ? 'bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-200'
      : 'bg-brand-500/10 text-brand-600 dark:text-brand-400'
  return (
    <div role="alert" className={`rounded-lg px-3 py-2 text-sm ${styles}`}>
      {children}
    </div>
  )
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="text-ink-400 inline-flex items-center gap-2 text-sm">
      <span
        aria-hidden
        className="border-ink-200 border-t-brand-500 h-3.5 w-3.5 animate-spin rounded-full border-2"
      />
      {label}
    </span>
  )
}

export function StatusPill({ status }: { status: string }) {
  const styles =
    {
      ready: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
      failed: 'bg-red-500/15 text-red-700 dark:text-red-300',
      processing: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
      pending: 'bg-ink-400/15 text-ink-600 dark:text-ink-200',
    }[status] ?? 'bg-ink-400/15 text-ink-600'

  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${styles}`}>
      {status}
    </span>
  )
}
