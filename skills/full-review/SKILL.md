---
name: full-review
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent
argument-hint: <pr-number> [--plan=<path>] [--report=<path>] [--review-only] [--at=<sha> --as-of=<iso8601>] [--no-ci-gate]
description: Nuclear Claude ↔ Codex review loop - Codex reviews the diff against the original requirements (issue body AND comments) and the implementation plan across four gates (correctness, requirements, architecture, security) plus a defect checklist; Claude fixes, repeat until approved; then mutation-checks the new tests, gates on exact-head CI, and posts an audit record to the PR
---

# Full Review: Nuclear Claude ↔ Codex Review Loop

You are entering an automated **nuclear review-fix loop** for **PR #$ARGUMENTS**.

Base: lhermoso/claude-workflows `full-review.md` (upstream commit ee4c805), plus the checks his reviews of
MuscleWiki PRs #432-#456 applied that the prompt alone did not encode. `docs/UPSTREAM-DIFF.md` in the
ai-skills repo lists every local delta.

## The Cycle

```
REQUIREMENTS ──► PLAN ──► DIFF ──► REVIEW (Codex, read-only) ──► FIXES (you) ──► MUTATION CHECK ──► CI ──► POST
```

The reviewer receives three inputs every round, all written to files by `scripts/gather_context.py`:

1. **Original requirements**: linked issues with **every comment** (a later comment can supersede the body), PR body and PR comments.
2. **Implementation plan**: `.pair/PLAN-<issue>.md` plus the Implementation Report. Optional; without it the architecture gate is N/A.
3. **The diff**: `MERGE_BASE_SHA..HEAD_SHA`, plus mechanically computed sibling-PR collisions and deployment topology (worker counts).

Approval requires **all four gates**, then a **mutation check** of the new tests, then **green CI on the exact head**:

1. **Correctness**: does the changed code work, on realistic paths?
2. **Requirements**: does the PR deliver the acceptance criteria of its linked issues and its own description?
3. **Architecture**: does the implementation follow the plan, or deviate silently? (N/A without a plan)
4. **Security**: does the diff introduce or leave open a realistic vulnerability?

## Severity Scale

| Level | Meaning | Blocks |
|---|---|---|
| **BLOCKER** | Breaks production or loses data on a realistic path; major exploit; an explicit requirement MISSING | yes |
| **HIGH** | Concrete bug on a common path; limited exploit or missing authz on a common path; explicit requirement PARTIAL; plan deviation breaking a contract/invariant; plan's primary test absent; a regression test that passes with its fix reverted | yes |
| **MEDIUM** | Needs uncommon conditions; perf regression without visible impact yet; missing edge-case test; hardening | no |
| **LOW** | Naming, style, cleanup | no |

Axis tags: `[CORRECTNESS]` `[AC]` `[ARCH]` `[SECURITY]` `[TEST]` `[CI]`. Legacy `[P1]/[P2]/[P3]` map to BLOCKER/HIGH/MEDIUM
(`/issue-pipeline` and `/drain-issues` call this loop; their "P1/P2" means "BLOCKER/HIGH").

## Rules

- **MAX_ITERATIONS = 6** review rounds. If unresolved after 6, remaining items go to a follow-up; do not keep looping. Never apply an unreviewed fix after the last round.
- **Codex never edits code.** It runs `--sandbox read-only`; it reads, reproduces, and reports.
- **Default scope:** fixes stay inside files the PR already changes. No unrelated refactors.
- **Stage explicit paths only.** Never `git add -A` / `git add .`: worktrees here carry untracked scripts, `.worktrees/`, `.pair/` records.
- **Bind everything to the snapshot.** Every result (review, mutation check, CI, post) belongs to one `HEAD_SHA`. Any push invalidates all of them.
- **Approval = Codex LGTM AND mutation check passed (or honestly recorded as not runnable) AND CI green on the exact head.** Local test runs never substitute for CI.
- **Always post** the final outcome to the PR (Phase 6), except with `--review-only`.
- **Never merge.** Never touch branch protection.

### Flags

- `--plan=<path>` / `--report=<path>`: override plan/report resolution.
- `--review-only`: one review round, no fixes, no push, no post. Prints the report. Use for audits and evals.
- `--at=<sha> --as-of=<iso8601>`: replay the review of an older commit with the PR body and comments as they were at that instant (implies `--review-only`). For evaluating this skill against historical reviews.
- `--no-ci-gate`: skip Phase 5; the best possible result is `APPROVED_NO_CI`, never `APPROVED`.

## Completeness / Security Carve-Out (overrides default scope)

The "stay inside the diff" rule is for code smells. It does **not** apply to requirement gaps, plan deviations, or security holes:

- A BLOCKER/HIGH finding tagged `[AC]`, `[ARCH]`, or `[SECURITY]` is **never** dismissible as scope expansion.
- You MAY touch non-diff files only for the *minimal direct fix* that (a) satisfies an explicit acceptance criterion, (b) restores an invariant the plan's Side-Effects Trace named, or (c) closes a realistic security hole on the PR's path.
- Broad, architectural, or risky fix → do not hack it in. Outcome **BLOCKED**, open a follow-up issue with a concrete remediation plan, report the PR as incomplete.

## Waivers Are the Owner's, Not the Author's

An explicit criterion is waived only by the issue author, a maintainer, or the repo owner, **in the issue or an issue comment**. A deviation that the PR body, the plan, the CHANGELOG, or a follow-up issue documents is disclosure, not a waiver: the criterion stays Partial/Missing and blocks (#447 zero-limit, #434/#449 "capture deferred to #420", #439 `.pair/` files outside a manifest-only scope). When the honest fix is "the owner amends the criterion", say exactly that and stop: do not code around it, and do not ask the reviewer to accept the PR body's rationale.

## Anti-Escalation Rule

When Codex raises a *correctness* finding on an axis it already raised, and the agreed fix is in place: **dismiss** it as same-axis escalation unless it names a concrete, different failure mode. Record it in history.

**Exemption:** anti-escalation and the "3+ stacked conditions → MEDIUM" downgrade never apply to `[AC]`, `[ARCH]`, `[SECURITY]`.

## Phase 0: Snapshot and Evidence

Shell state does not persist between Bash calls. Everything lives in a run directory with an `env.sh`; start
every later Bash block with `source "$RUN/env.sh"` (write `RUN` as a literal path).

`SKILL_DIR` is the base directory shown when this skill loaded (fallback: `readlink -f ~/.claude/skills/full-review`).

1. Pick the worktree. Never disturb the primary checkout and never stash or reset a dirty tree.

```bash
N=<pr-number>; SKILL_DIR=<skill base dir>
REPO_ROOT=$(git rev-parse --show-toplevel); REPO_NAME=$(basename "$REPO_ROOT")
RUN="$HOME/.cache/full-review/$REPO_NAME-pr$N"; mkdir -p "$RUN" && chmod 700 "$RUN"
HEAD_REF=$(gh pr view "$N" --json headRefName -q .headRefName)
git -C "$REPO_ROOT" fetch -q origin "pull/$N/head"          # FETCH_HEAD = PR head (works for forks too)
git -C "$REPO_ROOT" fetch -q origin "$HEAD_REF" 2>/dev/null  # origin/$HEAD_REF, needed to push fixes
# Reuse a worktree that already has the PR branch checked out and is clean; else make a dedicated one.
EXISTING=$(git -C "$REPO_ROOT" worktree list --porcelain | awk -v b="refs/heads/$HEAD_REF" '/^worktree /{w=$2} $0=="branch "b{print w}')
echo "existing worktree for $HEAD_REF: ${EXISTING:-none}"
```

   - `EXISTING` set and `git -C "$EXISTING" status --porcelain` empty → `WT=$EXISTING`.
   - `EXISTING` set but dirty → with `--review-only`, use a detached worktree (below); otherwise stop and tell the user which worktree is dirty.
   - None → `git -C "$REPO_ROOT" worktree add "$RUN/wt" "$HEAD_REF" 2>/dev/null || git -C "$REPO_ROOT" worktree add -b "$HEAD_REF" "$RUN/wt" "origin/$HEAD_REF"`, then `git -C "$RUN/wt" branch --set-upstream-to="origin/$HEAD_REF"` and `git -C "$RUN/wt" merge --ff-only "origin/$HEAD_REF"` (a stale local branch must catch up, or the snapshot check refuses); `WT=$RUN/wt`. A fork PR has no `origin/$HEAD_REF`: use `--review-only`.
   - `--review-only` / `--at`: `git -C "$REPO_ROOT" fetch -q origin "pull/$N/head" && git -C "$REPO_ROOT" worktree add --detach "$RUN/wt" "${AT_SHA:-FETCH_HEAD}"` (re-fetch first: the branch fetch above moved FETCH_HEAD).

2. Gather the evidence (pins `HEAD_SHA`, `BASE_SHA`, `MERGE_BASE_SHA`; refuses if local HEAD ≠ GitHub `headRefOid`):

```bash
python3 -I "$SKILL_DIR/scripts/gather_context.py" "$N" --worktree "$WT" --run-dir "$RUN" \
  [--plan=...] [--report=...] [--at "$AT_SHA" --as-of "$AS_OF"]
```

It writes `$RUN/context/`: `pr.md`, `issues.md` (bodies + all comments with author and association),
`plan.md` (`.pair/PLAN-<issue>.md` first; a bare `.pair/PLAN.md` only if it names a linked issue; in-house-api's
names #383), `report.md`, `siblings.md` (open PRs sharing files, same-numbered migrations, stacked base),
`deploy.md` (compose/Dockerfile/gunicorn worker counts), `patch.diff`, `history.md`, `snapshot.json`, and `$RUN/env.sh`.

3. Read `siblings.md`. A migration collision is reported to the user now: it is a merge-order problem
   neither PR's CI will catch (two leaves crash boot `migrate`).

## Phase 1: Codex Review

```bash
source "$RUN/env.sh"; R=<round number>
python3 -I "$SKILL_DIR/scripts/assemble_prompt.py" --run-dir "$RUN" > "$RUN/prompt-$R.md"
python3 -I "$SKILL_DIR/scripts/run_codex.py" --prompt "$RUN/prompt-$R.md" --cwd "$WT" --out "$RUN/round-$R"
echo "exit=$?"
```

Run it with `run_in_background` and wait for the notification: a round takes 3-15 minutes and the watchdog
allows up to 30 (kills on 420 s without output, retries once). The prompt is `review-prompt.md`; edit that file,
not this one, to change what Codex checks.

`run_codex.py` uses `codex exec - --sandbox read-only --ephemeral --json --cd "$WT"`. **Never `codex exec review`**:
it rejects `--sandbox`, hangs silently, and returned a clean 249-character review where plain `exec` found two
real defects. A zero-finding review from it is no evidence. `codex exec` rejects `--title` and `--full-auto`.

## Phase 2: Parse

Read `$RUN/round-$R/result.json` and print `$RUN/round-$R/review.md` in full (findings, AC matrix, defect checklist).

| exit | outcome | action |
|---|---|---|
| 0 | `LGTM` (no BLOCKER/HIGH) | → Phase 4 (mutation check) |
| 10 | `CHANGES_REQUESTED` | → Phase 3 (`--review-only`: stop and report) |
| 11 | `BLOCKED` | Count findings first. Real BLOCKER/HIGH needing a broad fix → outcome BLOCKED, prepare follow-up issue. Zero findings and the prose blames tooling → infra failure, treat as 12 |
| 12 | `INCONCLUSIVE` | Empty, partial, killed, or the verdict line contradicts its own findings. Read `stderr_tail`. Never approve. If `parse_status` is `partial` and the streamed text has BLOCKER/HIGH findings, triage them as in Phase 3; otherwise rerun the round once, then stop INCONCLUSIVE |

Format drift: a finding missing reproduction or proposed correction is still triaged on its merits; it cannot be
BLOCKER until Codex supplies a reproduction (cap at HIGH; the cap never applies to `[AC]`/`[SECURITY]`).

`worktree_touched: true` means something changed the worktree during a read-only review: inspect before going on.

## Phase 3: Triage and Fix

**Verify every BLOCKER/HIGH yourself before acting**: open the quoted code, confirm the reproduction is real.
Then classify:

- **`[AC]` / `[ARCH]` / `[SECURITY]`** → carve-out applies: minimal direct fix, even in a non-diff file. Broad → BLOCKED. Never dismiss as scope or escalation.
  - `[AC]` whose real fix is an owner decision (amend the criterion, supply evidence only a human can capture, e.g. a real sandbox payload) → do not fake it. Report it as needing the owner, leave it blocking.
  - `[ARCH]` where the deviation is better and breaks no recorded invariant → RECORD it (PR body, history, the plan file) instead of reverting.
- **Same-axis correctness escalation** → DISMISS (Anti-Escalation Rule).
- **Correctness-only scope expansion** → DISMISS, note a follow-up.
- **Genuine correctness / test bug** → fix the root cause.
- **MEDIUM / LOW** → NOTE. Fix only as a one-liner in a file you are already editing. Batch MEDIUMs into one follow-up issue at the end.

**Before editing any file, state these facts.** Asking yourself "am I sure?" does not work: self-evaluation
always answers yes. Naming a concrete fact forces a real Grep/Read, and that investigation changes the output.

1. Every file that imports or calls the symbol you are about to change (grep the tree, not memory).
2. The public functions, endpoints, or exported behaviour the change affects.
3. If the file reads or writes a data file: the field names, structure, and date format actually present.
4. The Codex finding, quoted verbatim, not your paraphrase.

Fact 4 catches the failure this loop keeps producing: a fix for a restated version of the finding instead of the
finding itself (memory: `full_review_fixes_introduce_false_claims`, `verification_claims_name_tests_that_dont_exist`).

For each fix: start from the proposed correction but fix the root cause; every `[CORRECTNESS]` fix with a
reproduction gets a test encoding that scenario, written to fail before the fix. Then:

```bash
source "$RUN/env.sh"
git -C "$WT" status --porcelain            # review the list; stage ONLY the files you changed
git -C "$WT" add -- <path> <path> ...
git -C "$WT" diff --cached --stat
git -C "$WT" commit -m "fix: address full-review round $R

- [HIGH][AC] AC-1 <title> -> <what changed, file:line>
- [HIGH][CORRECTNESS] F-2 <title> -> <what changed>

Co-Authored-By: <current model attribution from the session's system reminder>"
git -C "$WT" push
```

Append to `$RUN/context/history.md` (replace the iteration-1 placeholder the first time), one line per finding:

```
Round N (head <short sha>):
- [HIGH][AC] AC-1 <criterion> -> FIXED: <change, file:line> + <test name>
- [HIGH][ARCH] F-3 <deviation> -> RECORDED: <better path, no invariant broken; plan updated>
- [HIGH][CORRECTNESS] F-4 <issue> -> DISMISSED (escalation): same axis as round K F-2
- [HIGH][CORRECTNESS] F-5 <issue> -> DISMISSED (out-of-scope): needs <file>; follow-up
- [BLOCKER][AC] AC-2 <criterion> -> NEEDS OWNER: <decision required>; still blocking
- [MEDIUM][TEST] TEST-6 <issue> -> NOTED
```

Then rerun **Phase 0 step 2** (the head moved: new snapshot, history is kept) and **Phase 1** with the next round.
If every blocker was dismissed and nothing changed, still run a fresh round before approval.

## Phase 4: Mutation Check of the New Tests

A test that passes with its fix removed proves nothing (#437: a wall-clock fixture landed on the month boundary;
#432: one GROWTH fixture where the plan had nine). After LGTM, before CI:

1. List the tests this PR adds or changes for its behavior (from `patch.diff`). None, or a docs/deps-only PR → record "mutation check: N/A (no behavioral tests)" and go to Phase 5.
2. Make a throwaway worktree at the reviewed head and record the primary tree's state:
   ```bash
   source "$RUN/env.sh"
   BEFORE=$(git -C "$WT" status --porcelain | sha256sum)
   git -C "$WT" worktree add --detach "$RUN/mut" "$HEAD_SHA"
   ```
3. **Control:** run those tests in `$RUN/mut` unmodified, with the repo's real runner (repo CLAUDE.md / memory: in-house-api runs Django tests in `musclewiki-django-dev`-style containers or `docker run` with an absolute `:ro` mount of `$RUN/mut`, frontend via `node:24`). They must pass. If they cannot run locally, record why and skip to step 6.
4. **Mutant:** revert the production hunks (not the tests) in `$RUN/mut`:
   `git -C "$RUN/mut" diff "$MERGE_BASE_SHA" "$HEAD_SHA" -- <production files> | git -C "$RUN/mut" apply -R`
   (one file or hunk at a time when a whole-PR revert breaks imports). Rerun the same tests.
5. Each test must now **fail on an assertion**. A collection/import error means the mutant was wrong, not that the test is good: narrow the revert. A test that still passes → `[HIGH][TEST]` finding (MEDIUM if other tests cover the same criterion and fail): back to Phase 3.
6. Clean up and prove the primary tree was untouched (memory `mutation_harness_writes_real_tree`):
   `git -C "$WT" worktree remove --force "$RUN/mut"; [ "$(git -C "$WT" status --porcelain | sha256sum)" = "$BEFORE" ] || echo "PRIMARY TREE CHANGED"`

Time-box it to 20 minutes. "Not run" is a legitimate recorded result; claiming it ran when it did not is not.

## Phase 5: CI Gate (exact head)

```bash
source "$RUN/env.sh"
python3 -I "$SKILL_DIR/scripts/ci_gate.py" --repo "$REPO" --sha "$HEAD_SHA" --base "$BASE_BRANCH" \
  --wait 1800 --grep-logs '<new regression test name>' '<another>'
```

Run it in the background (it polls). It reads check runs for the SHA (gh 2.45 has no `gh pr checks --json`),
required contexts from **rulesets and** classic protection, and every executed job's log.

- `CI_GREEN` → continue. Skipped push-only jobs are listed as not run and are **not** described as passing.
- `CI_FAILED: <checks>` → pull the log (`gh run view --job <id> --log-failed`), write a `[BLOCKER][CI]` finding, back to Phase 3, then a fresh review round (a CI fix is code). A failure that reproduces on the base branch may be rerun once (`gh run rerun <run-id> --failed`); still failing → finish `CI_FAILED`.
- `CI_MISSING: <contexts>` → not approved. Diagnose before reporting (memory): a stacked PR whose base is not `main` runs zero tests; a merge conflict silently skips CI; a `paths:` filter makes a suite green by not running. Do not substitute local runs, do not touch branch protection.
- `logs.*.patterns`: every new regression test name must appear in an executed job's log. Absent → the test did not run in CI: say so; treat as `CI_MISSING` for that test.
- `logs.*.soft_fail_errors` inside passing jobs (non-gating mypy, dependency audit): report them as soft-failing, never as clean.
- Report the test counts from `logs.*.summary` (e.g. "1,687 passed").

## Phase 6: Post to the PR (always, unless `--review-only`)

Outcomes: `APPROVED`, `APPROVED_NO_CI`, `CHANGES_REQUESTED`, `BLOCKED`, `MAX_ITERATIONS_REACHED`, `CI_FAILED`,
`CI_MISSING`, `INCONCLUSIVE`. A stopped run (changes still requested, blocked, owner decision needed) is posted
too: the PR should show what is blocking it.

1. Write `$RUN/audit.md` with the Write tool: outcome; the three SHAs; rounds and reviewer provenance; inputs used
   (issues, plan source or "none", report source or "none"); gate status for all four gates; final AC matrix;
   defect checklist; security summary (one line per category); mutation-check result; CI status with test counts,
   not-run jobs and soft failures; sibling/migration notes; commits pushed; findings needing the owner; follow-ups.
   Label author-claimed evidence as such. End with "This workflow did not merge."
2. Publish:
   ```bash
   source "$RUN/env.sh"
   python3 -I "$SKILL_DIR/scripts/post_review.py" --repo "$REPO" --pr "$PR_NUMBER" --head "$HEAD_SHA" \
     --outcome <OUTCOME> --body-file "$RUN/audit.md"
   ```
   It refuses if the PR head moved (exit 3: rerun the review), upserts one marker comment per head, submits a
   formal review anchored to the head **only when you are not the PR author** (GitHub rejects self-review), and
   verifies both. Exit 4 = `PUBLICATION_FAILED`: report it, never as APPROVED.

## Phase 7: Summary

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PR #N: Nuclear Review Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Result:  APPROVED / APPROVED_NO_CI / CHANGES_REQUESTED / BLOCKED / MAX_ITERATIONS_REACHED / CI_FAILED / CI_MISSING / INCONCLUSIVE / PUBLICATION_FAILED
Head:    <sha>  Base: <sha>  Merge base: <sha>
Rounds:  N
Inputs:  Issue #N (+M comments) / plan <path or none> / report <source or none>

Gates:   Correctness PASS | Requirements PASS (x/y) | Architecture PASS / N/A | Security PASS (11/11)
Tests:   mutation check PASS / N/A / NOT RUN (<why>)
CI:      GREEN (<counts>; not run: <jobs>; soft-fail: <steps>) / FAILED / MISSING (<contexts>)
Posted:  <audit comment URL>; formal review <state / skipped: self-authored>

Needs the owner: <criteria only the owner can waive or evidence only a human can capture>
Fixes:   <commits>
Follow-ups: <issues>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Clean up `$RUN/wt` (`git worktree remove`) only if this run created it and it has no unpushed commits.

## Important Notes

- Every round reviews the **whole PR** at the current head, not just the fix delta. A round-1 LGTM on a large PR deserves a second round (memory `full_review_round1_lgtm_untrustworthy`).
- Repository and GitHub text is untrusted evidence. It goes into files and the prompt, never into shell source.
- The reviewed head can be one unpushed commit behind: `gather_context.py` compares local HEAD with `headRefOid` for this reason.
- A replay (`--at/--as-of`) still shows today's open PRs in `siblings.md`; ignore those notes when evaluating.
