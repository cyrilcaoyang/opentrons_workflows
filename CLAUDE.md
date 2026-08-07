# CLAUDE.md — Claude Code notes

Start with **[`AGENTS.md`](AGENTS.md)** — the shared, model-agnostic
instruction file (binding contract pointers, repo layout, working conventions,
memory policy). Everything there applies to Claude too. This file adds only
what is specific to Claude Code.

@./AGENTS.md

## Binding contract

[`AGENT_RULES.md`](AGENT_RULES.md) (this repo's rules, linking to the canonical
[`ac-organic-lab/docs/AGENT_RULES.md`](../ac-organic-lab/docs/AGENT_RULES.md))
and [`ac-organic-lab/docs/STATUS_SPEC.md`](../ac-organic-lab/docs/STATUS_SPEC.md)
are binding and take precedence. Do not weaken, bypass, or rewrite them unless
the human explicitly asks. See `AGENTS.md` §1.

## Claude-specific

- **Repo memory dir:**
  `~/.claude/projects/-mnt-c-Users-sdl2-Projects-opentrons-server/memory/`.
  One fact per file with frontmatter; index each in that dir's `MEMORY.md`.
  Follow the type rules: `project` / `feedback` / `user` / `reference`.
  Cross-repo or device-PC-wide facts do **not** go here — propose them for the
  user's global memory instead (`AGENTS.md` §5).
- **Slash commands / skills** are listed at session start; invoke a skill only
  when it appears in the available list. Don't guess skill names.
- **Workspace context:** the parent [`../CLAUDE.md`](../CLAUDE.md) describes
  this machine — the **xarmpc device PC**, which serves devices and does not
  aggregate or orchestrate. Treat it as reference, not as Claude-only workflow
  notes. Note its guidance assumes PowerShell; Claude Code here usually runs
  under **WSL bash**, so translate rather than copying commands verbatim.
- **Running commands from WSL:** there is no `uv` on the WSL `PATH` — use
  `/mnt/c/SDL_Tools/uv.exe`, or run the Windows interpreter directly
  (`./.venv.test/Scripts/python.exe -m pytest tests/unit -q`, the preferred way
  to run tests without touching the live services' `.venv/`).
- **Interactive elevation is unavailable** from a headless shell: UAC prompts
  render only inside an RDP session, so an elevated command hangs rather than
  failing. Ask the human to run those with `!` in the prompt instead.
