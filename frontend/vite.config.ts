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
    // 5174/8001 rather than Vite's 5173 and uvicorn's 8000, because gecko-notes uses
    // both of those and the two apps are routinely run side by side. Vite would have
    // drifted to 5174 on its own when 5173 was taken, but then which app is on which
    // port depends on start order; pinning makes it decidable.
    port: 5174,
    proxy: {
      // Same-origin in production (nginx proxies both), so the app never needs to
      // know an API host. The dev server mimics that rather than using CORS.
      //
      // This target must track the port the dev backend is started on. Pointing it at
      // 8000 while GAM runs on 8001 does not fail cleanly: it reaches gecko-notes'
      // backend, which shares JWT_SECRET_KEY and so verifies GAM's token and answers.
      // The result is another app's data rendered as if it were this one's.
      '/api': { target: 'http://localhost:8001', changeOrigin: true, ws: true },
      '/media': { target: 'http://localhost:8001', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: true,
  },
})
