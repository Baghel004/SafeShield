# SafeShield frontend

React + TypeScript client for the SafeShield API.

```bash
npm install
npm run dev      # http://localhost:5173
```

The dev server proxies `/api` to `http://localhost:8000`, so the browser sees a
single origin. That matters more than it looks: the refresh token is an
httpOnly cookie, and cross-origin requests would not carry it, so sessions
would silently fail to restore on reload while everything else appeared to
work. Point the proxy elsewhere with `VITE_API_PROXY`.

| Script | Does |
|---|---|
| `npm run dev` | Dev server with the API proxy |
| `npm run build` | Typecheck, then production build to `dist/` |
| `npm run test` | Vitest |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc` with no emit |

## How auth works

The access token is held **in memory only**. Not `localStorage` — anything
there is readable by any script that ends up on the page, so a single XSS
becomes a stolen session that outlives the tab. Losing the token on reload is
the intended behaviour; the httpOnly refresh cookie is what restores the
session, and `AuthProvider` asks for a new access token before deciding whether
to show the login screen.

On a 401 the client refreshes once and replays the request. Concurrent 401s
share **one** refresh, because refresh tokens rotate and replaying a rotated
one is treated as theft — the backend revokes the whole token family. A page
firing four queries on mount would otherwise log the user out at the moment the
token expired. `src/lib/api.test.ts` asserts exactly that.

## Streaming

`EventSource` cannot be used: it only issues GET requests and cannot set an
`Authorization` header, and the chat endpoint is a POST carrying a bearer
token. So the response body is read with `fetch` and parsed by hand in
`src/lib/sse.ts`.

The parser exists because network chunk boundaries have nothing to do with
message boundaries — one event can arrive split across three reads, and three
can arrive in one. Its tests cover split events, CRLF endings, comments,
keep-alives, and a multi-byte character split mid-character.

A failed stream arrives as an in-band `error` event rather than an HTTP status,
because by then the response has already begun. A failure therefore looks like
a request that succeeded and stopped early; watching for that event is the only
way to tell.

## Deploying

Static output, so any static host works. `vercel.json` covers Vercel: SPA
rewrites so `/chat` resolves on reload, immutable caching for fingerprinted
assets, and no caching for `index.html`.

Set `VITE_API_BASE_URL` to the API origin at build time, and add that origin to
`CORS_ORIGINS` on the backend — the browser blocks the request before it is
sent otherwise.
