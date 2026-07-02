import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Relative base so the built index.html loads its assets over file://
  // inside the Electron shell.
  base: "./",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173
  }
});
