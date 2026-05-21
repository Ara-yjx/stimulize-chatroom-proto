import { webcrypto } from 'node:crypto'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Some WSL/Linux Node setups don't expose Web Crypto globally the way Vite 6 expects.
// Use Node's built-in Web Crypto implementation when `globalThis.crypto` is missing.
if (!globalThis.crypto?.getRandomValues) {
  Object.defineProperty(globalThis, 'crypto', {
    value: webcrypto,
    configurable: true,
  })
}

export default defineConfig({
  plugins: [react()],
  server: { port: 3000 },
})
