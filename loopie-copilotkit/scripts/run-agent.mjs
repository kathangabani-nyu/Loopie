import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = process.platform === "win32";
const script = isWin
  ? path.join(root, "scripts", "run-agent.bat")
  : path.join(root, "scripts", "run-agent.sh");

const child = spawn(script, [], {
  cwd: root,
  stdio: "inherit",
  shell: isWin,
});

child.on("exit", (code) => process.exit(code ?? 1));
