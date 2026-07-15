import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    watch: {
      // Docker Desktop on Windows doesn't forward native filesystem change
      // events for bind-mounted volumes, so chokidar's default watcher never
      // fires here — fall back to polling.
      usePolling: true,
    },
  },
})
