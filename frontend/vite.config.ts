import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.BDO_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: api, ws: true } } },
  preview: { proxy: { "/api": { target: api, ws: true } } },
});
