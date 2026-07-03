# Agents Configuration (Workspace)

This repository is developed with agent-assisted tools (Claude Code) under one human owner.

Primary instructions: `.github/copilot-instructions.md`.

## What This Repo Contains

- OpenClaw skills for Jarvis (in `skills/`)
- SOUL.md personality configuration
- OpenClaw setup and configuration
- Project documentation

## Rules for All Agents

- Follow issue hierarchy: Milestone -> Epic -> Task/Bug.
- Always link implementation PRs to one issue (`Closes #NNN`).
- Respect branch naming convention and small-PR policy.
- Skills are the deliverable — each skill is a directory with SKILL.md.
- `.github/` workflows are dev process tools, NOT Jarvis features.
- Do not introduce out-of-scope work without explicit approval.

## Active Scope

- P1: OpenClaw setup + PM skills (triage, reporting, issue health)
- Next: Research skills

## Out of Scope (Current)

- Multi-user / team features
- Paid LLM APIs as primary
- Cloud hosting
- Plugin marketplace
- Mobile app
