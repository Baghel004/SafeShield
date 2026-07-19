import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
// vitest's defineConfig, not vite's -- it is the one that accepts the `test`
// block. Vite's own overloads reject it, and keeping one config file means the
// dev server and the test runner cannot drift apart.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Proxy in development so the browser sees one origin. Without it every
    // request is cross-origin and the refresh cookie -- httpOnly, SameSite=lax
    // -- is not sent, so sessions silently fail to restore on reload while
    // everything else appears to work.
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
