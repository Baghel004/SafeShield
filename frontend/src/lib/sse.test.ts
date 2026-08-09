import { describe, expect, it } from 'vitest'

import { parseSse, type SseEvent } from './sse'

/** A stream that hands out exactly the chunks given, as the network would. */
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

async function collect(chunks: string[]): Promise<SseEvent[]> {
  const out: SseEvent[] = []
  for await (const event of parseSse(streamOf(chunks))) out.push(event)
  return out
}

describe('parseSse', () => {
  it('parses a single event', async () => {
    expect(await collect(['event: token\ndata: {"text":"hi"}\n\n'])).toEqual([
      { event: 'token', data: '{"text":"hi"}' },
    ])
  })

  it('parses several events from one chunk', async () => {
    const events = await collect([
      'event: citations\ndata: {"grounded":true}\n\nevent: token\ndata: {"text":"a"}\n\n',
    ])
    expect(events.map((e) => e.event)).toEqual(['citations', 'token'])
  })

  it('reassembles an event split across chunks', async () => {
    // The case that motivates the buffer: network chunk boundaries have
    // nothing to do with message boundaries.
    expect(await collect(['event: tok', 'en\ndata: {"te', 'xt":"split"}\n\n'])).toEqual([
      { event: 'token', data: '{"text":"split"}' },
    ])
  })

  it('does not emit a partial event', async () => {
    // No terminating blank line and no data yet -- nothing may be yielded,
    // otherwise the UI renders half a token.
    expect(await collect(['event: token\n'])).toEqual([])
  })

  it('emits a trailing event with no blank line', async () => {
    expect(await collect(['event: done\ndata: {}'])).toEqual([{ event: 'done', data: '{}' }])
  })

  it('handles CRLF line endings', async () => {
    // The spec allows them and a proxy may rewrite to them.
    expect(await collect(['event: token\r\ndata: x\r\n\r\n'])).toEqual([
      { event: 'token', data: 'x' },
    ])
  })

  it('defaults the event name to message', async () => {
    expect(await collect(['data: bare\n\n'])).toEqual([{ event: 'message', data: 'bare' }])
  })

  it('ignores comments and keep-alives', async () => {
    expect(await collect([': keep-alive\n\nevent: token\ndata: x\n\n'])).toEqual([
      { event: 'token', data: 'x' },
    ])
  })

  it('joins multi-line data with newlines', async () => {
    expect(await collect(['data: one\ndata: two\n\n'])).toEqual([
      { event: 'message', data: 'one\ntwo' },
    ])
  })

  it('strips only the single framing space after the colon', async () => {
    // 'data:  x' carries a leading space as real content.
    expect(await collect(['data:  x\n\n'])).toEqual([{ event: 'message', data: ' x' }])
  })

  it('decodes a multi-byte character split across chunks', async () => {
    // '€' is three bytes; splitting it must not produce replacement chars.
    const encoded = new TextEncoder().encode('data: €\n\n')
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoded.slice(0, 7))
        controller.enqueue(encoded.slice(7))
        controller.close()
      },
    })
    const out: SseEvent[] = []
    for await (const event of parseSse(stream)) out.push(event)
    expect(out).toEqual([{ event: 'message', data: '€' }])
  })

  it('stops when the signal is aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    const out: SseEvent[] = []
    for await (const event of parseSse(streamOf(['data: x\n\n']), controller.signal)) {
      out.push(event)
    }
    expect(out).toEqual([])
  })
})
