import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The engine serves dist/ under a strict CSP: no inline scripts, no data: URIs.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    sourcemap: false,
  },
});
