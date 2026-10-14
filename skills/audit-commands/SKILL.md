---
name: audit-commands
allowed-tools: Bash(ls:*), Bash(wc:*), Bash(head:*), Bash(grep:*), Bash(diff:*), Read, WebSearch
argument-hint: [command-name] (optional - audit one command instead of all)
description: Audit the hand-written commands in ~/.claude/commands for overlap, staleness, and scope drift, and return a verdict with named evidence for each
---

<!--
  Rubric source: affaan-m/ECC (github.com/affaan-m/ecc), skills/skill-stocktake/SKILL.md
  Taken from npm ecc-universal@2.2.1, 2026-09-22. MIT licensed.
  Rubric only - ECC's three bash scripts were deliberately NOT copied. Their scan.sh
  is hardcoded to `find -name "SKILL.md"` under ~/.claude/skills, and its usage-frequency
  columns read ~/.claude/observations.jsonl, an artifact only an ECC hook writes. Here
  those columns would read 0 forever. For ~12 files, reading them directly is better.
  ECC itself is NOT installed - no plugin, no hooks, no marketplace entry.
-->

# Audit Commands

Review the hand-written commands in `~/.claude/commands/` and return a verdict per command,
each backed by named evidence. With an argument, audit only that command.

## Phase 1 — Enumerate

```bash
ls -la ~/.claude/commands/*.md
wc -lw ~/.claude/commands/*.md | sort -rn
```

Note anything that is not a live command: `.bak`, `.bak-preECC`, or two files whose
descriptions overlap enough that the model could not reliably choose between them.

Then read each command in full. Do not audit from the frontmatter alone — the failure modes
this catches live in the body.

## Phase 2 — Judge

This is holistic judgment, not a score. Four dimensions:

- **Actionability** — concrete steps, commands, and paths the reader can act on immediately, not a description of an approach.
- **Scope fit** — the filename, the `description:`, and the body agree on what this command does. Drift between them is the most common defect: the body grows, the description does not.
- **Uniqueness** — the value is not already covered by `CLAUDE.md`, by `MEMORY.md` and its memory files, or by another command.
- **Currency** — every tool, flag, path, and file the command names still exists. Use WebSearch for third-party CLI flags; use Bash to confirm anything local.

For each command, check explicitly:

- [ ] Overlap with the other commands in the directory
- [ ] Overlap with `CLAUDE.md` and with `MEMORY.md` + its memory files
- [ ] Every referenced path, script, flag, and tool still exists
- [ ] Instructions do not contradict a recorded memory

That fourth check matters most here. Several memories record that a command's own prescribed
step was wrong in practice. A command that contradicts a memory is worse than no command —
it re-introduces a bug already paid for once.

## Phase 3 — Verdict

| Verdict | Meaning |
|---|---|
| **Keep** | Useful and current |
| **Improve** | Worth keeping; specific change needed |
| **Update** | A referenced tool, flag, or path has changed |
| **Retire** | Low value, stale, or superseded |
| **Merge into [X]** | Substantial overlap; name the target |

### Reason quality — the part that makes this worth running

A verdict without evidence is noise. Every `reason` must be self-contained and
decision-enabling. Never write a bare label.

- **Retire** — state (1) the specific defect and (2) what covers the same need instead.
  - Bad: `"Superseded"`
  - Good: `"Steps 2-5 duplicate fix-issue.md exactly; its only unique content is the --plan-review flag, which issue-pipeline.md already documents."`
- **Merge** — name the target and the content to move.
  - Bad: `"Overlaps with X"`
  - Good: `"Thin at 60 lines; the whole body is Phase 1 of drain-issues.md. Move the 'batch sizing' note into that file and delete this one."`
- **Improve** — name the section, the change, and the target size where relevant.
  - Bad: `"Too long"`
  - Good: `"430 lines; the 'Codex handoff' section (L180-260) restates full-review.md. Cut it and link instead, reaching ~340 lines."`
- **Keep** — restate the rationale. Never write `"Unchanged"`.
  - Good: `"Only command covering the stacked-PR rebase path; matches stacked_pr_rebase_squash_merged_base and references no dead paths."`

## Phase 4 — Report and consolidate

Output one table:

| Command | Lines | Verdict | Reason |
|---|---|---|---|

Then, for every **Retire** or **Merge**, present the full justification — defect, replacement,
and blast radius (which other commands, memories, or workflows reference it) — and **stop for
explicit confirmation**. Never delete or rewrite a command without it.

For **Improve** and **Update**, present the specific change and let the user decide.

Finally, report whether `MEMORY.md` is near its size limit, and if so say plainly that the
fix is shortening index lines, not deleting memories.

## Notes

- Judge every command the same way regardless of who wrote it or where it came from.
- Back up before editing any command that is large or heavily tuned: `cp x.md x.md.bak-<reason>`, then `diff` after.
- Deleting is always the user's call.
