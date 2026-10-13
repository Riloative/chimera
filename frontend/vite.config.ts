/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const backend = process.env.CHIMERA_DEV_BACKEND ?? "http://127.0.0.1:8700";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: {
      // Same-origin in dev too: the backend never enables CORS.
      "/api": { target: backend, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    // Fonts, KaTeX fonts and all JS are emitted as local files: no CDN at runtime.
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (!id.includes("node_modules")) return undefined;
          if (/katex|micromark|mdast|hast|unified|remark|rehype|lowlight|highlight\.js|react-markdown|vfile|unist/.test(id)) return "markdown";
          if (/react|scheduler|zustand|@radix-ui|lucide/.test(id)) return "vendor";
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    globals: true,
  },
});
