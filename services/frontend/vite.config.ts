import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // injectManifest (not generateSW) because offline log sync needs a
      // real hand-authored service worker (src/sw.ts) — per-request
      // correlation ids, a custom onSync outcome handler, BroadcastChannel
      // messaging — none of which generateSW's declarative config supports.
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'autoUpdate',
      injectRegister: false, // registerSW() is called explicitly in main.tsx
      // The frontend's Dockerfile only ever runs `npm run dev` — there's no
      // prod build/serve path anywhere in docker-compose.yml. Without this,
      // the service worker simply doesn't exist in this repo's only running
      // configuration.
      devOptions: {
        enabled: true,
        type: 'module',
      },
      manifest: {
        name: 'ARIA',
        short_name: 'ARIA',
        description: 'Household operations tracker',
        theme_color: '#171717',
        background_color: '#ffffff',
        display: 'standalone',
        icons: [{ src: 'favicon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
      },
    }),
  ],
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
