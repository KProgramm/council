import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig, type ProxyOptions } from 'vite'

const councilApiTarget = process.env.COUNCIL_API_TARGET ?? 'http://127.0.0.1:8000'

// The API rejects requests whose Origin differs from its own host, so the proxy rewrites it.
type Req = { getHeader: (name: string) => unknown; setHeader: (name: string, value: string) => void }
const sameOrigin: NonNullable<ProxyOptions['configure']> = (proxy) => {
  const rewrite = (request: Req) => { if (request.getHeader('origin')) request.setHeader('origin', councilApiTarget) }
  proxy.on('proxyReq', rewrite)
  proxy.on('proxyReqWs', rewrite)
}
const route = (ws = false): ProxyOptions => ({ target: councilApiTarget, changeOrigin: true, ws, configure: sameOrigin })

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    proxy: {
      '/reviews': route(),
      '/ws': route(true),
      '/replay': route(true),
      '/fixtures': route(),
      '/github': route(),
      '/settings': route(),
      '/watch': route(),
    },
  },
})
