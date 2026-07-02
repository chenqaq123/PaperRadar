// Records this machine's repo + venv paths into local-paths.json so the
// packaged .app (self-use build) knows where to find the Python backend.
// Run automatically before `npm run app:build`.
const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..", "..");
const python =
  process.platform === "win32"
    ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
    : path.join(repoRoot, ".venv", "bin", "python");

const config = { repoRoot, python };
const out = path.join(__dirname, "local-paths.json");
fs.writeFileSync(out, JSON.stringify(config, null, 2));
console.log("[paper-radar] wrote", out, config);
