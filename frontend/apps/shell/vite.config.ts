import federation from '@originjs/vite-plugin-federation'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// Remote entry URLs are relative to the gateway; public/config.json can override or add
// remotes at runtime without rebuilding the shell.
export default defineConfig({
  base: '/',
  plugins: [
    vue(),
    federation({
      name: 'shell',
      remotes: {
        admin: '/mfe/admin/assets/remoteEntry.js',
        curves: '/mfe/curves/assets/remoteEntry.js',
      },
      shared: ['vue', 'vue-router', 'element-plus'],
    }),
  ],
  build: {
    target: 'esnext',
    modulePreload: false,
    outDir: 'dist',
    chunkSizeWarningLimit: 2000,
    watch: process.env.VITE_WATCH_POLL ? { chokidar: { usePolling: true, interval: 500 } } : null,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8080',
      '/auth': 'http://localhost:8080',
      '/mfe': 'http://localhost:8080',
    },
  },
})
