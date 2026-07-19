// Server-Sent Events over fetch.
//
// EventSource is the obvious tool and cannot be used here: it only issues GET
// requests and cannot set an Authorization header. The chat endpoint is a POST
// carrying a bearer token, so the stream has to be read from fetch's body and
// parsed by hand.

export interface SseEvent {
  event: string
  data: string
}

/**
 * Split a byte stream into SSE events.
 *
 * The hard part is that network chunks have nothing to do with message
 * boundaries: one event can arrive split across three reads, and three events
 * can arrive in one. So bytes accumulate in a buffer and only complete
 * records -- those terminated by a blank line -- are emitted. Anything left
 * over stays buffered for the next read.
 */
export async function* parseSse(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (signal?.aborted) return
      const { done, value } = await reader.read()
      if (done) break

      // `stream: true` keeps a multi-byte character split across two chunks
      // from being decoded as two replacement characters.
      buffer += decoder.decode(value, { stream: true })

      // Normalise line endings before splitting: the spec allows CRLF, and a
      // proxy may rewrite them.
      buffer = buffer.replace(/\r\n/g, '\n')

      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const raw = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const parsed = parseRecord(raw)
        if (parsed) yield parsed
        boundary = buffer.indexOf('\n\n')
      }
    }

    // A final record with no trailing blank line still counts.
    const parsed = parseRecord(buffer)
    if (parsed) yield parsed
  } finally {
    // Releasing matters on the abort path: without it the connection is held
    // open after the component that started it has gone.
    reader.releaseLock()
  }
}

function parseRecord(raw: string): SseEvent | null {
  if (!raw.trim()) return null

  let event = 'message'
  const dataLines: string[] = []

  for (const line of raw.split('\n')) {
    if (line.startsWith(':')) continue // comment / keep-alive
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    // One optional space after the colon is part of the framing, not the data.
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') event = value
    else if (field === 'data') dataLines.push(value)
  }

  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}
