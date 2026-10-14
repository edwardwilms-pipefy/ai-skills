# ai-skills

Personal agent skills, kept in one place so they survive machine changes and switching or adding LLM tools.

## Layout

```
skills/<name>/SKILL.md      canonical skills, open Agent Skills format (one folder per skill;
                            scripts and prompts live next to their SKILL.md)
upstream/lhermoso/          git submodule: lhermoso/claude-workflows, pinned, reference only
config/claude/              global CLAUDE.md, RTK.md, settings.template.json (org specifics stripped)
docs/UPSTREAM-DIFF.md       which skills fork which upstream file, and every local delta
install.sh                  symlinks skills into each tool's skills directory
```

Why this shape:
- **One format, many tools.** `<dir>/<name>/SKILL.md` is read by Claude Code (`~/.claude/skills`) and Codex (`~/.codex/skills`). A new tool that reads the same layout needs one line in `install.sh`'s `TARGETS`.
- **Symlinks, not copies.** Edit a skill here, and every tool sees it at once. `git log` is the history.
- **Upstream stays pristine.** The submodule is never edited; `docs/UPSTREAM-DIFF.md` says how ours differ.
- **Tool-specific bits are labelled.** Skill bodies mention Claude Code tools (Agent, Bash, `$ARGUMENTS`) and the Codex CLI where they need them. Other agents read these as instructions; adjust a skill when a tool lacks a feature.

## Install

```bash
git clone --recurse-submodules git@github.com:edwardwilms/ai-skills.git ~/Documentos/GitHub/ai-skills
cd ~/Documentos/GitHub/ai-skills
./install.sh                         # Claude Code: ~/.claude/skills/<name> -> skills/<name>
./install.sh --codex                 # Codex
./install.sh --codex --upstream-codex  # plus lhermoso's gh-workflow-suite for Codex
./install.sh --dry-run --claude      # preview
./install.sh --uninstall --claude    # remove only links that point here
```

Claude Code invokes a skill as `/<name>` exactly like the old `~/.claude/commands/<name>.md`. Do not keep both:
`install.sh` warns about a leftover `~/.claude/commands/<name>.md`.

Global config is not linked automatically (settings carry machine-specific choices). Copy by hand:
`config/claude/CLAUDE.md` and `RTK.md` → `~/.claude/`; merge `settings.template.json` into `~/.claude/settings.json`.

## Skills

| Skill | What it does |
|---|---|
| `full-review` | Claude ↔ Codex review loop: 4 gates (correctness, requirements, architecture, security) + defect checklist, mutation check of new tests, exact-head CI gate, posts an audit record. Scripts in `skills/full-review/scripts/` |
| `issue-pipeline` | Issue → plan → fix → PR → review |
| `drain-issues` | Dependency-aware batch processing of the issue backlog |
| `batch-issues` | Parallel issue processing without dependency analysis |
| `fix-issue` | Issue → worktree → TDD fix → PR, with a pre-edit fact gate |
| `quick-fix` | Fast autonomous fix |
| `create-issue` | Well-structured GitHub issue with acceptance criteria |
| `commit` / `pr` | Conventional commit; detailed PR with changelog |
| `review-pr` / `review-changes` | PR merge-safety review; diff regression review |
| `scan-debt` | Tech-debt and code-smell scan |
| `audit-commands` | Audit skills for overlap, staleness, drift |
| `context-budget` | Rank what is consuming the context window |

## Adding a skill

1. `mkdir skills/<name>` and write `skills/<name>/SKILL.md` with `name:` and `description:` frontmatter (`argument-hint`, `allowed-tools` optional).
2. `./install.sh` (re-run is safe).
3. Commit.

## Never commit

Credentials (`~/.claude/.credentials.json`), config backups, session history, project memory, `.env` files.
