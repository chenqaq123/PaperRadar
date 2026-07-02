// Exposes the backend base URL to the renderer without enabling nodeIntegration.
const { contextBridge } = require("electron");

const arg = process.argv.find((a) => a.startsWith("--paper-radar-api-base="));
const apiBase = arg ? arg.split("=")[1] : "http://127.0.0.1:8000";

contextBridge.exposeInMainWorld("paperRadar", {
  apiBase,
  isDesktop: true,
});
