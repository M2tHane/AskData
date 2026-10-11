import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // SSE needs an unbuffered proxy; the API is the only backend origin.
    // /mcp (exactly: /mcp-clients is a page) is the MCP endpoint for external agents.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '^/mcp/?$': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'] },
})
