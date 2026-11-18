import federation from '@originjs/vite-plugin-federation'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  base: '/mfe/admin/',
  plugins: [
    vue(),
    federation({
      name: 'admin',
      filename: 'remoteEntry.js',
      exposes: { './manifest': './src/manifest.ts' },
      shared: ['vue', 'vue-router', 'element-plus'],
    }),
  ],
  build: {
    target: 'esnext',
    modulePreload: false,
    cssCodeSplit: false,
    outDir: 'dist/mfe/admin',
    chunkSizeWarningLimit: 2000,
    watch: process.env.VITE_WATCH_POLL ? { chokidar: { usePolling: true, interval: 500 } } : null,
  },
  // Standalone dev mode (npm run dev): talks to the dockerised gateway.
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8080', '/auth': 'http://localhost:8080' },
  },
})
