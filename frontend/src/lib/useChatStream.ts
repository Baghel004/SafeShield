import { useCallback, useRef, useState } from 'react'

import { ApiError, chat } from './api'
import { parseSse } from './sse'
import type { Citation } from './types'

export interface ChatTurn {
  id: string
  question: string
  answer: string
  citations: Citation[]
  grounded: boolean
  streaming: boolean
  error: string | null
}

/**
 * Drives one streamed answer at a time.
 *
 * The endpoint sends `citations` first, then `token` repeatedly, then `done`.
 * On failure it sends an `error` event rather than an HTTP status, because by
 * then the response has already begun and the status line is long gone -- so
 * a failed answer looks like a successful request that stopped early, and the
 * only way to tell is to watch for that event.
 */
export function useChatStream() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const update = useCallback((id: string, patch: Partial<ChatTurn>) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)))
  }, [])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
  }, [])

  const ask = useCallback(
    async (question: string, documentId: string | null) => {
      const id = crypto.randomUUID()
      setTurns((prev) => [
        ...prev,
        {
          id,
          question,
          answer: '',
          citations: [],
          grounded: false,
          streaming: true,
          error: null,
        },
      ])
      setStreaming(true)

      const controller = new AbortController()
      abortRef.current = controller

      try {
        const resp = await chat.stream(question, documentId, controller.signal)
        if (!resp.body) throw new Error('The server sent no response body.')

        // Tokens arrive faster than React should re-render. Buffering them and
        // flushing on a frame keeps a long answer from causing one render per
        // word, which visibly stutters.
        let buffered = ''
        let frame: number | null = null
        const flush = () => {
          frame = null
          const text = buffered
          setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, answer: text } : t)))
        }

        for await (const event of parseSse(resp.body, controller.signal)) {
          if (event.event === 'citations') {
            const data = JSON.parse(event.data) as { grounded: boolean; citations: Citation[] }
            update(id, { citations: data.citations, grounded: data.grounded })
          } else if (event.event === 'token') {
            buffered += (JSON.parse(event.data) as { text: string }).text
            frame ??= requestAnimationFrame(flush)
          } else if (event.event === 'error') {
            const data = JSON.parse(event.data) as { message: string }
            update(id, { error: data.message })
          }
        }

        if (frame !== null) cancelAnimationFrame(frame)
        update(id, { answer: buffered, streaming: false })
      } catch (err) {
        if (controller.signal.aborted) {
          update(id, { streaming: false })
        } else {
          update(id, {
            streaming: false,
            error:
              err instanceof ApiError
                ? err.message
                : 'The answer could not be generated. Please try again.',
          })
        }
      } finally {
        abortRef.current = null
        setStreaming(false)
      }
    },
    [update],
  )

  return { turns, streaming, ask, stop }
}
