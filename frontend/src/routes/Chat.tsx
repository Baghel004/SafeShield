import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'

import { Alert, Button } from '../components/ui'
import { documents } from '../lib/api'
import type { Citation } from '../lib/types'
import { useChatStream, type ChatTurn } from '../lib/useChatStream'

const SUGGESTIONS = [
  'What happens if I do not make any claim for a whole year?',
  'Is cataract treatment excluded from cover?',
  'How quickly do I have to tell you about a claim?',
  'Can I claim the cost of an ambulance?',
]

export function Chat() {
  const { turns, streaming, ask, stop } = useChatStream()
  const [question, setQuestion] = useState('')
  const [scope, setScope] = useState<string>('')
  const endRef = useRef<HTMLDivElement>(null)

  const { data: docs } = useQuery({ queryKey: ['documents'], queryFn: documents.list })
  const ready = (docs ?? []).filter((d) => d.status === 'ready')

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  function submit(e: FormEvent) {
    e.preventDefault()
    const trimmed = question.trim()
    if (trimmed.length < 3 || streaming) return
    setQuestion('')
    void ask(trimmed, scope || null)
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col px-4">
      <div className="flex-1 space-y-8 overflow-y-auto py-8">
        {turns.length === 0 ? (
          <Empty onPick={(q) => setQuestion(q)} />
        ) : (
          turns.map((turn) => <Turn key={turn.id} turn={turn} />)
        )}
        <div ref={endRef} />
      </div>

      <form onSubmit={submit} className="sticky bottom-0 pb-6">
        <div className="border-ink-200 dark:border-ink-800 dark:bg-ink-900 rounded-2xl border bg-white p-2 shadow-sm">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter is a newline. A textarea is used
              // rather than an input so a long question stays readable.
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit(e)
              }
            }}
            rows={2}
            placeholder="Ask about a policy — waiting periods, exclusions, claim deadlines…"
            className="w-full resize-none bg-transparent px-3 py-2 text-sm outline-none"
          />
          <div className="flex items-center justify-between gap-2 px-1">
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              className="text-ink-600 dark:text-ink-400 max-w-[60%] truncate rounded-lg bg-transparent px-2 py-1 text-xs outline-none"
              aria-label="Limit the search to one document"
            >
              <option value="">All documents</option>
              {ready.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title ?? d.filename}
                </option>
              ))}
            </select>

            {streaming ? (
              <Button type="button" variant="ghost" onClick={stop}>
                Stop
              </Button>
            ) : (
              <Button type="submit" disabled={question.trim().length < 3}>
                Ask
              </Button>
            )}
          </div>
        </div>
        <p className="text-ink-400 mt-2 text-center text-xs">
          Answers come only from the indexed policy text. Not financial or legal advice.
        </p>
      </form>
    </div>
  )
}

function Empty({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="py-12 text-center">
      <h1 className="text-lg font-semibold tracking-tight">Ask about a policy</h1>
      <p className="text-ink-600 dark:text-ink-400 mx-auto mt-2 max-w-md text-sm">
        Every answer is built from the policy text that was retrieved for your question, with a
        citation for each claim. If the documents do not cover it, it says so instead of guessing.
      </p>
      <div className="mx-auto mt-6 grid max-w-lg gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="border-ink-200 dark:border-ink-800 hover:border-brand-500 rounded-xl border px-4 py-2.5 text-left text-sm transition-colors"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

function Turn({ turn }: { turn: ChatTurn }) {
  return (
    <article className="space-y-3">
      <h2 className="text-base font-medium">{turn.question}</h2>

      {turn.error ? (
        <Alert>{turn.error}</Alert>
      ) : (
        <div
          className={`text-ink-800 dark:text-ink-100 text-sm leading-relaxed whitespace-pre-wrap ${
            turn.streaming && !turn.answer ? 'text-ink-400' : ''
          } ${turn.streaming && turn.answer ? 'streaming-caret' : ''}`}
        >
          {turn.answer || (turn.streaming ? 'Searching the policies…' : '')}
        </div>
      )}

      {turn.citations.length > 0 && <Citations citations={turn.citations} />}

      {!turn.streaming && !turn.error && !turn.grounded && turn.answer && (
        <p className="text-ink-400 text-xs">
          No matching policy text was found, so no answer was generated.
        </p>
      )}
    </article>
  )
}

function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<number | null>(null)

  return (
    <div className="border-ink-200 dark:border-ink-800 border-t pt-3">
      <p className="text-ink-400 mb-2 text-xs font-medium">Sources</p>
      <ul className="space-y-1.5">
        {citations.map((c) => (
          <li key={c.index}>
            <button
              type="button"
              onClick={() => setOpen(open === c.index ? null : c.index)}
              className="hover:text-brand-500 flex w-full items-baseline gap-2 text-left text-xs"
              aria-expanded={open === c.index}
            >
              <span className="text-brand-500 font-mono">[{c.index}]</span>
              <span className="text-ink-600 dark:text-ink-400 truncate">
                {c.filename} · p.{c.page_no}
                {c.section_path && ` · ${c.section_path}`}
              </span>
            </button>
            {open === c.index && (
              <p className="text-ink-600 dark:text-ink-400 border-ink-200 dark:border-ink-800 mt-1.5 ml-6 border-l-2 py-1 pl-3 text-xs leading-relaxed">
                {c.excerpt}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
