# Tooling Setup Notes

These notes capture the intended agent-tooling setup. Treat external command names and package names as verification targets before relying on them in a demo.

## What Can Be Set Up Safely in the Repo

- `AGENTS.md` for Codex-style repo instructions.
- `.agents/skills/*/SKILL.md` for Codex project skills.
- `.claude/skills/*/SKILL.md` for Claude project skills.
- Markdown guidance for MCP setup and demo safety.

## What Must Be Verified Before Use

- Exact Claude plugin installation commands.
- Exact Redis MCP package invocation.
- Exact Codex MCP config key names for the installed Codex version.
- Exact AG-UI and CopilotKit package APIs at implementation time.

## MCP Policy

Keep the MCP surface tight because this project may touch secrets, Redis, local files, shell commands, and GitHub state.

Recommended MCP categories:

- GitHub MCP for issues, PRs, repo inspection, commits, and release notes.
- Filesystem MCP scoped only to the Loopie repo path.
- Redis MCP for inspecting keys, streams, memory artifacts, and routing artifacts.
- Docs or web access for Weave, Redis, CopilotKit, AG-UI, and LangGraph docs.
- Custom Loopie MCP later for demo actions like `run_baseline`, `approve_correction`, `run_patched`, and `eval_compare`.

## Custom Loopie MCP Later

Expose only high-value demo actions:

```text
/run/baseline
/approve-correction
/run/patched
/eval/compare
```

Do not build this before the normal API demo path works.

