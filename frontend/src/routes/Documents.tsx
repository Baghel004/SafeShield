import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { Alert, Button, Spinner, StatusPill } from '../components/ui'
import { ApiError, documents } from '../lib/api'
import type { Document } from '../lib/types'

const ACTIVE: Document['status'][] = ['pending', 'processing']

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function Documents() {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['documents'],
    queryFn: documents.list,
    // Upload returns 202 and the work happens in a background worker, so the
    // list is polled while anything is still being ingested -- and only then.
    // A fixed interval would keep polling an idle page forever.
    refetchInterval: (query) =>
      query.state.data?.some((d) => ACTIVE.includes(d.status)) ? 2000 : false,
  })

  const upload = useMutation({
    mutationFn: documents.upload,
    onSuccess: () => {
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : 'Upload failed. Try again.'),
  })

  const remove = useMutation({
    mutationFn: documents.remove,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['documents'] }),
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : 'Could not delete that document.'),
  })

  const docs = data ?? []
  const mine = docs.filter((d) => !d.is_shared)
  const shared = docs.filter((d) => d.is_shared)

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Documents</h1>
          <p className="text-ink-600 dark:text-ink-400 mt-1 text-sm">
            Upload a policy PDF and it is indexed in the background. Questions search everything
            listed here.
          </p>
        </div>
        <>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) upload.mutate(file)
              e.target.value = '' // so re-picking the same file fires again
            }}
          />
          <Button onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
            {upload.isPending ? 'Uploading…' : 'Upload PDF'}
          </Button>
        </>
      </div>

      {error && (
        <div className="mb-4">
          <Alert>{error}</Alert>
        </div>
      )}

      {isLoading ? (
        <Spinner label="Loading documents…" />
      ) : (
        <div className="space-y-8">
          <Section
            title="Your documents"
            empty="Nothing uploaded yet."
            docs={mine}
            onDelete={(id) => remove.mutate(id)}
            deletingId={remove.isPending ? remove.variables : null}
          />
          <Section
            title="Sample policies"
            empty="No shared documents."
            docs={shared}
            hint="Available to everyone. These cannot be deleted."
          />
        </div>
      )}
    </div>
  )
}

function Section({
  title,
  docs,
  empty,
  hint,
  onDelete,
  deletingId,
}: {
  title: string
  docs: Document[]
  empty: string
  hint?: string
  onDelete?: (id: string) => void
  deletingId?: string | null
}) {
  return (
    <section>
      <h2 className="text-ink-600 dark:text-ink-200 text-sm font-semibold">{title}</h2>
      {hint && <p className="text-ink-400 mt-0.5 text-xs">{hint}</p>}

      {docs.length === 0 ? (
        <p className="text-ink-400 mt-3 text-sm">{empty}</p>
      ) : (
        <ul className="border-ink-200 dark:border-ink-800 mt-3 divide-y divide-ink-200 rounded-xl border dark:divide-ink-800">
          {docs.map((doc) => (
            <li key={doc.id} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{doc.title ?? doc.filename}</p>
                <p className="text-ink-400 mt-0.5 text-xs">
                  {formatSize(doc.size_bytes)}
                  {doc.status === 'ready' && doc.chunk_count > 0 && (
                    <> · {doc.page_count} pages · {doc.chunk_count} chunks</>
                  )}
                </p>
                {doc.status === 'failed' && doc.error && (
                  <p className="mt-1 text-xs text-red-600">{doc.error}</p>
                )}
              </div>

              <StatusPill status={doc.status} />

              {onDelete && (
                <Button
                  variant="danger"
                  className="px-2 py-1 text-xs"
                  onClick={() => onDelete(doc.id)}
                  disabled={deletingId === doc.id}
                  aria-label={`Delete ${doc.filename}`}
                >
                  {deletingId === doc.id ? 'Deleting…' : 'Delete'}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
