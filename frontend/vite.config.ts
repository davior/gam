/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
    // Note: gecko-notes carries a 14-package ProseMirror `dedupe` list and a
    // treeshake override here, both load-bearing for BlockNote. GAM has no editor,
    // so neither applies — don't copy them back in without a reason.
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      // Same-origin in production (nginx proxies both), so the app never needs to
      // know an API host. The dev server mimics that rather than using CORS.
      '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
      '/media': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: true,
  },
})
