import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API runs separately in development (`uvicorn app.main:app --reload`); the dev
// server forwards API calls to it, so the browser sees one origin as in production.
const api = process.env.PYQXEL_API ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api/v1/ws': { target: api.replace(/^http/, 'ws'), ws: true },
      '/api': api,
      '/health': api,
    },
  },
  build: {
    // The ECharts chunk (charts, axes, tooltips, heatmap) is about 220 kB gzipped.
    chunkSizeWarningLimit: 700,
    // ECharts is large; keep it in its own long-cached chunk.
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/echarts') || id.includes('node_modules/zrender')) {
            return 'echarts'
          }
          return undefined
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
