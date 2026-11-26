import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: { tsconfigPaths: true },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          minSize: 16_000,
          // Automatic splitting keeps shared Markdown dependencies out of the optional plugin chunks.
          groups: [
            { name: 'vendor-motion', test: /node_modules[\\/]motion/ },
            { name: 'vendor-three', test: /node_modules[\\/](three|thinking-orbs)/ },
            { name: 'vendor-metal', test: /node_modules[\\/]metal-fx/ },
          ],
        },
      },
    },
  },
  server: { proxy: { '/api': { target: process.env.SYNAPSE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true } } },
})
