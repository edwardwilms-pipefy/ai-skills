# Relationship to upstream (lhermoso/claude-workflows)

`upstream/lhermoso` is a git submodule pinned at the commit we last compared against.
It is **reference only**: `install.sh` never installs its commands (they share 11 names
with ours). Its Codex skill `gh-workflow-suite` can be installed with `--upstream-codex`.

Update and compare:

```bash
git submodule update --remote upstream/lhermoso      # pull his latest
git -C upstream/lhermoso log --oneline -5
diff -u upstream/lhermoso/full-review.md skills/full-review/SKILL.md | less
git add upstream/lhermoso && git commit -m "chore(upstream): bump lhermoso to <sha>"
```

Pinned at: `ee4c805` (2026-09-24).

## Per skill

| Skill | Upstream file | State |
|---|---|---|
| batch-issues, commit, create-issue, pr, quick-fix, review-changes, review-pr, scan-debt | same name `.md` | identical body (only `name:` added to frontmatter) |
| fix-issue | `fix-issue.md` | upstream + local **Pre-Edit Fact Gate** (name callers, interfaces, data shape, quote the instruction; rollback line before destructive commands) |
| drain-issues | `drain-issues.md` | **older** upstream copy (2026-05-15). Local edits: `--full-auto` removed (codex 0.160 rejects it), the inline review now defers to `/full-review`, severities renamed BLOCKER/HIGH. Upstream's newer version (gates on by default, fable/opus routing, Phase 5.5) not adopted yet |
| issue-pipeline | `issue-pipeline.md` | same as drain-issues: older copy + the same three local fixes |
| full-review | `full-review.md` | rebuilt on upstream ee4c805, then extended (below) |
| audit-commands, context-budget | none | local, adapted from affaan-m/ECC (MIT) |

## full-review: local deltas over upstream

Derived from lhermoso's actual reviews of MuscleWiki PRs #432-#456 (2026-10), i.e. what his
`gh-workflow-suite` runs caught that the command-version prompt did not encode:

1. **Waivers are the owner's.** A deviation documented only by the PR author (body, plan, CHANGELOG, follow-up issue) does not waive an explicit criterion (#447, #434, #449, #439). Upstream's "recorded non-blocking rationale" loophole is closed.
2. **Issue constraints are criteria**: allowed-file lists, required defaults, required evidence (#439).
3. **Evidence provenance**: a fixture marked derived/constructed fails a "captured payload" criterion (#434, #449).
4. **Deployment topology** is computed (`deploy.md`: compose, Dockerfile, gunicorn worker counts) and the prompt makes Codex check process-local state against it (#449: per-worker cache with 4 workers).
5. **Stale caller instances** written back by a full `save()` (#435).
6. **Mutation check** (Phase 4): revert the production hunks in a throwaway worktree; each new regression test must fail on an assertion (#437, #432).
7. **Sibling PRs**: open PRs sharing files, same-numbered migrations vs base and vs open PRs, stacked base (#449/#443).
8. **Deployment wiring** as part of delivery: env var reaches every service, dark-launch defaults, CHANGELOG deployment notes (#448, #449).
9. **CI gate for gh 2.45**: check runs by exact SHA (no `gh pr checks --json`), required contexts from rulesets + classic protection, skipped push-only jobs reported as not run (never "passed"), regression-test names grepped in executed job logs, soft-failing steps (non-gating mypy) reported, test counts quoted.
10. **Always post**: one idempotent marker comment per head; a formal review only when the actor is not the PR author (GitHub rejects self-review); refuses on a moved head.
11. **Plan resolution**: `.pair/PLAN-<issue>.md` first; a bare `.pair/PLAN.md` only if it names a linked issue (in-house-api's is #383 and was wrongly inherited).
12. **Codex call**: `codex exec - --sandbox read-only --ephemeral --json --cd <worktree>` (upstream uses workspace-write for its gateway self-test; we tell Codex not to use the gateway and run tests ourselves in Phase 4).
13. **Deterministic scripts** instead of shell snippets (`scripts/`): shell state does not persist between agent Bash calls, and untrusted PR/issue text never reaches shell source. Issue comments, closing refs via GraphQL (gh 2.45 lacks the JSON field), stable finding IDs, evidence labelled verified vs author-claimed.
14. **Replay mode** (`--at` + `--as-of`) to evaluate the skill against historical reviews, with the PR body and comments as they were at the time.
15. Explicit-path staging (upstream still has `git add -A`).
