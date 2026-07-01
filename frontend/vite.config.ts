import path from "path"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

// Native dev: http://127.0.0.1:8000. Docker dev: VITE_DEV_API_PROXY=http://backend:8000
const apiTarget = process.env.VITE_DEV_API_PROXY || "http://127.0.0.1:8000"
const wsTarget = apiTarget.replace(/^http/, "ws")

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  optimizeDeps: {
    include: ['three', '@react-three/fiber', '@react-three/drei', 'troika-three-text'],
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    watch: {
      usePolling: process.env.CHOKIDAR_USEPOLLING === "true",
    },
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      // Gemini Live voice proxy (Django Channels WebSocket).
      '/ws': {
        target: wsTarget,
        ws: true,
        changeOrigin: true,
      },
    },
  },
})