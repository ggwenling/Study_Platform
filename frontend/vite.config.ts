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
          groups: [
            { name: 'vendor-motion', test: /node_modules[\\/]motion/ },
            { name: 'vendor-three', test: /node_modules[\\/](three|thinking-orbs)/ },
            { name: 'vendor-metal', test: /node_modules[\\/]metal-fx/ },
            { name: 'vendor-highlight', test: /node_modules[\\/](highlight\.js|rehype-highlight)/ },
            {
              name: 'vendor-markdown',
              test: /node_modules[\\/](react-markdown|remark-|rehype-|unified|micromark|mdast|vfile|bail|trough)/,
            },
          ],
        },
      },
    },
  },
  server: { proxy: { '/api': { target: process.env.SYNAPSE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true } } },
})