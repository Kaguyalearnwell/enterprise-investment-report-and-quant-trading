import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Development: `uvicorn backend.api.app:app --port 8000` in one terminal, `npm run dev` here.
// Production: `npm run build`; the API serves frontend/dist itself.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
