import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5177,
    proxy: {
      "/api-local": {
        target: "http://127.0.0.1:8788",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api-local/, ""),
      },
    },
  },
});