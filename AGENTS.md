# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, ZCode, etc.) when working with code in this repository.

## What this repository is

A standalone agent skill — **cowork-recall** — that gives AI coding agents read-only, cross-agent access to the user's local session history: keyword search across 13 AI coding tools, paginated history browsing, full-conversation detail view, and daily/weekly/any-range work summaries cross-checked against project git logs. Published for installation via `npx skills add 5iFish/cowork-recall-skill`. Runtime scripts are Python 3.8+ standard-library only; there is no build, lint, or test tooling in this repo (tests live in the development workspace).

## Structure

The skill lives in `skills/cowork-recall/`:

- `SKILL.md` is the entry point. Its YAML frontmatter contains `name` and `description`; the body defines the capability routing (search / list / detail / summary), shared conventions (time ranges, source filters, pagination, exit codes), and the output red lines (never fabricate sessions, commits, conclusions, or numbers).
- `scripts/session_recall.py` is the retrieval CLI (`search` / `list` / `detail`). stdout is JSON; exit codes: 0 ok, 1 bad args, 2 source unavailable or fatal schema incompatibility, 3 session not found (detail only).
- `scripts/work_summary.py` is the summary CLI. stdout JSON v3 (`window/sources/sessions/stats.by_source/degradations/truncated/meta`); `--format markdown` renders the fixed four-section report via `markdown_renderer.py`.
- `scripts/adapters/` holds per-engine adapters behind the interface in `common.py` (registry in `__init__.py` lists all 13 engines in `ORDER`). Trae's adapter uses the sibling `trae_ipc_bridge.js` to read sessions from a running Trae local service. Add a new engine by adding one adapter here.
- `scripts/install_skill.py` optionally links/copies the skill into `~/.agents/skills/cowork-recall` for agents that read that shared directory.

## Authoring conventions

- All data access is local and read-only. Never add network calls, and never run git write commands — the git cross-check is `git log` only.
- Keep scripts dependency-free (standard library only) so the skill works on a bare Python install.
- Preserve the exit-code contract and the degradation model: one engine failing must never take down the others.
- If you change the JSON shape or CLI surface, update SKILL.md in the same commit.
- This repo is a publish target synced from the development workspace; prefer content edits there and re-sync.
