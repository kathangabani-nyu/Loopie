# Local Dev Lessons — Getting Loopie Running

Brief postmortem from the first local run of `loopie-copilotkit/`. Use this to avoid the same traps when wiring the real app (Redis, Weave, Postgres, CopilotKit, LangGraph).

---

## 1. Repo & secrets (before anyone clones)

| Problem | What happened | Avoid |
|---|---|---|
| Nested git repo | `loopie-copilotkit/` had its own `.git`, so the monorepo would have pushed a submodule pointer instead of source files | One `.git` at repo root; remove nested `.git` before first push |
| Internal files on GitHub | `AGENTS.md`, `.agents/`, `skills-lock.json` were in the initial commit | Keep agent/editor config local; add to root `.gitignore` early |
| `.env` leakage | Real keys live in `loopie-copilotkit/.env` | Only commit `.env.example`; verify with `git check-ignore` before push |

---

## 2. Next.js / Tailwind / Windows

| Problem | What happened | Avoid |
|---|---|---|
| **Turbopack + Tailwind v4** | `npm run dev` with `--turbopack` hung forever on `○ Compiling / ...` | Use `next dev --webpack` on Windows until Turbopack + `lightningcss` native binaries are stable |
| **PostCSS shape** | Calling `@tailwindcss/postcss` as a function broke Webpack (`Malformed PostCSS Configuration`) | Use the v4 object form in `postcss.config.mjs`: `plugins: { "@tailwindcss/postcss": {} }` |
| **OneDrive path** | Project under `OneDrive\Desktop\Loopie` caused slow compiles (~30–45s), `.next` lock/ENOTEMPTY errors, constant file watcher noise | Develop from a non-synced path (e.g. `C:\dev\Loopie`); exclude `node_modules` and `.next` from sync |
| **First page load** | Browser timed out while dev server compiled | Expect a long first compile; don’t assume “broken” until you see `GET / 200` in the terminal |

---

## 3. Ports & stale processes

| Problem | What happened | Avoid |
|---|---|---|
| Port 3000 in use | UI fell back to 3001; user opened wrong URL | Before `npm run dev`: `Get-NetTCPConnection -LocalPort 3000,8123` and kill stale PIDs, or `Remove-Item -Recurse -Force .next` + restart |
| Port 8123 in use | LangGraph agent crashed: `OSError: Port 8123 is already in use` | Same — kill leftover `node` / `python` from prior runs |
| Document ports | Three services, not one | **3000** UI · **8123** LangGraph · **8001** Loopie API (`npm run dev:loopie`) |

---

## 4. Python / LangGraph on Windows

| Problem | What happened | Avoid |
|---|---|---|
| Unicode log noise | `UnicodeEncodeError` on `→` and `⚠️` in agent logs (cp1252 console) | Set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` in `run-agent.bat` / shell profile |
| EOL warning | `langgraph-api 0.7.101` end-of-life warnings | Plan upgrade; not blocking for local mock demo |
| Slow graph import | ~8s startup on first load | Normal for dev; don’t treat as failure |

---

## 5. UI looked broken but wasn’t

| Problem | What happened | Avoid |
|---|---|---|
| **Chat vs App mode** | Loopie cockpit (Seed, Baseline, …) lives in the **App** panel; in **Chat** mode the panel is `w-0` (~48px) — buttons exist in DOM but don’t behave | Default to App mode for Loopie work; document the top-right **Chat / App** toggle |
| **`npm run dev` ≠ full stack** | `npm run dev` starts UI + LangGraph only; cockpit calls `/api/loopie/*` → `localhost:8001` | Always run `npm run dev:loopie` in a second terminal; gate UI with a clear error if `:8001` is down |
| **Silent failures** | Cockpit swallowed API errors; panels stayed empty | Show status/errors in UI; check `curl http://localhost:8001/health` |
| **State / events bug** | `export_state()` only read `swarm` + `corrections` streams, not `evals` — Seed looked like it did nothing | Merge all Redis event streams (`evals`, `swarm`, `corrections`) when exporting cockpit state |

---

## 6. Infrastructure that *did* work

- `docker compose up` (Redis + Postgres + CopilotKit Intelligence) when `COPILOTKIT_LICENSE_TOKEN` is set
- Mock LLM mode (`LOOPIE_LLM_MODE=mock`) — no OpenAI key required for the reliability demo path
- API layer itself was fine once reachable; issues were tooling, layout, and missing second process

---

## Recommended local startup (checklist)

```powershell
# Terminal 1 — infra (optional, if license token set)
cd loopie-copilotkit
docker compose up -d redis postgres   # or full compose for Threads

# Terminal 2 — Loopie API (required for cockpit)
npm run dev:loopie

# Terminal 3 — UI + LangGraph
Remove-Item -Recurse -Force .next -ErrorAction SilentlyContinue
npm run dev
```

Open **http://localhost:3000** → **App** mode → run Seed → Baseline → Propose → Approve → Rerun.

---

## Takeaways for the “real” build

1. **Treat dev as three processes** (UI, agent, Loopie API) until merged into one orchestrator script.
2. **Windows + OneDrive + Turbopack** is a bad combo; standardize on Webpack for dev on Win32.
3. **Fail loud in the UI** when Redis / Loopie API / agent URL is unreachable.
4. **Don’t hide the primary demo surface** behind Chat/App mode without onboarding.
5. **Pin and document ports** in `.env.example` and README.
6. **Keep secrets and agent skills local** from day one of the public repo.
