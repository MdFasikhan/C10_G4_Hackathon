import { defineConfig } from 'vite'

// The default CSS minifier (lightningcss) rejects one UnoCSS-generated rule, so CSS is left unminified.
export default defineConfig({
  build: { cssMinify: false },
})
