#!/usr/bin/env python3
"""Run one task on the Codex fleet at a chosen model and effort.

    python codex_task.py --prompt "rename X to Y across the repo" --tier cheap
    python codex_task.py --prompt-file - --model gpt-6.1-sol --effort high
    python codex_task.py --review --base origin/staging --tier frontier
    python codex_task.py --list-models

WHY CODEX IS A FIRST-CLASS FLEET HERE

It is logged in by ChatGPT subscription (verified 2026-10-21: ~/.codex/auth.json
carries tokens and a null OPENAI_API_KEY), so its marginal cost is zero dollars
and its scarce resource is quota - quota that is INDEPENDENT of Claude's. Idle
Codex capacity while the Claude window drains is the single biggest waste this
router exists to fix.

WHAT WAS INHERITED FROM skills/full-review/scripts/run_codex.py

That script already paid for these lessons; re-deriving them would be throwing
the receipts away:

  * prompt on stdin via `codex exec -`, with --sandbox read-only --ephemeral
  * NEVER `codex exec review`: it rejects --sandbox, hangs silently, and has
    returned clean reviews that dropped real findings. --review below builds a
    review PROMPT for plain `exec` instead.
  * a stall watchdog, because Codex can go >150s between events while reasoning
  * one retry, then give up with a status that says "inconclusive", not "clean"

WHAT WAS MEASURED HERE, 2026-10-21

  * `codex exec` has NO --effort flag. Effort goes through the config override
    `-c model_reasoning_effort=<level>`. The key is real: it appears in
    codex.exe and not in the code-mode host. Passing an invalid VALUE to it does
    NOT error, and neither does a wholly invented key - so -c cannot tell you
    whether it worked. That is why the level is validated locally, against
    models_cache.json, before the process starts.
  * token floor ~2,742 for a 5-token prompt. Worth knowing before sending many
    small tasks, though it is 4x cheaper than the Gemini CLI's ~12,200.
  * --output-last-message writes the final answer to a file, which is far more
    robust than digging it out of the JSONL stream. The JSONL is still written,
    because the watchdog needs something that grows.

Exit: 0 ok, 10 the model answered but flagged a problem, 12 inconclusive
(empty, partial, killed), 2 refused locally (bad model/effort).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODELS_CACHE = Path.home() / ".codex" / "models_cache.json"
STALL_S = 420.0   # inherited: Codex goes >150s between events while reasoning
MAX_S = 1800.0
POLL_S = 5.0


def load_models() -> list[dict]:
    try:
        return json.loads(MODELS_CACHE.read_text(encoding="utf-8")).get("models", [])
    except (OSError, ValueError):
        return []


def validate(model: str, effort: str | None) -> str | None:
    """Return an error string, or None. Checked HERE because -c cannot tell us.

    A silently-ignored effort override is the worst outcome: the run looks
    successful, the ledger records an effort that was never applied, and the
    measurements built on it are quietly wrong.
    """
    models = load_models()
    if not models:
        return None  # cache unreadable: cannot judge, so do not block
    entry = next((m for m in models if m.get("slug") == model), None)
    if entry is None:
        slugs = ", ".join(sorted(m.get("slug", "") for m in models if m.get("visibility") != "hide"))
        return f"model {model!r} is not in models_cache.json. Available: {slugs}"
    if effort:
        levels = [lv.get("effort") for lv in entry.get("supported_reasoning_levels", [])]
        if effort not in levels:
            return f"model {model!r} does not support effort {effort!r}. Supports: {', '.join(levels)}"
    return None


def tier_spec(tier: str) -> tuple[str, str]:
    """Model and effort for a tier, read from routes.v1.json - the same file the
    policy uses, so the executor cannot drift from the decision."""
    import route as route_mod
    spec = route_mod.load_json(route_mod.ROUTES_FILE)["fleets"]["codex"]["tiers"][tier]
    return spec["model"], spec.get("effort")


def build_args(model, effort, sandbox, cd, jsonl, last_msg, schema=None) -> list[str]:
    # NOT the bare name: `codex` on PATH is a .ps1 here and Popen cannot exec it.
    # cli.resolve finds the vendored native binary. See cli.py for the measurement.
    import cli
    args = [
        *cli.resolve("codex"), "exec", "-",
        "--sandbox", sandbox,
        "--ephemeral",
        "--skip-git-repo-check",
        "--json",
        "--color", "never",
        "-m", model,
        "-o", str(last_msg),
    ]
    if effort:
        args += ["-c", f"model_reasoning_effort={effort}"]
    if cd:
        args += ["--cd", str(cd)]
    if schema:
        args += ["--output-schema", str(schema)]
    return args


def attempt(prompt, args, jsonl, last_msg, *, stall, max_s, label):
    """Run once with a stall watchdog. Returns "exited" or "killed".

    The watchdog measures growth of the JSONL stream, not wall time alone: a
    model that is reasoning hard looks identical to one that has hung, and only
    the event stream tells them apart.
    """
    with open(jsonl, "w", encoding="utf-8") as fout, \
            open(str(jsonl) + ".err", "w", encoding="utf-8") as ferr:
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=fout, stderr=ferr, text=True,
            encoding="utf-8", errors="replace",
        )
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass
        started = last_change = time.time()
        last_size = 0
        while proc.poll() is None:
            time.sleep(POLL_S)
            try:
                size = jsonl.stat().st_size
            except OSError:
                size = last_size
            if size > last_size:
                last_size, last_change = size, time.time()
            now = time.time()
            if now - last_change >= stall or now - started >= max_s:
                print(f"[{label}] watchdog kill: stalled={now - last_change:.0f}s "
                      f"elapsed={now - started:.0f}s", file=sys.stderr)
                proc.kill()
                proc.wait()
                return "killed"
    return "exited"


def final_message(last_msg, jsonl):
    """(status, text). Prefers -o; falls back to digging through the JSONL."""
    try:
        text = last_msg.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return "ok", text
    except OSError:
        pass
    msgs = []
    try:
        for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            item = ev.get("item", {}) if ev.get("type") == "item.completed" else {}
            if item.get("type") == "agent_message" and item.get("text"):
                msgs.append(item["text"])
    except OSError:
        pass
    if msgs:
        return "partial", "\n\n---\n\n".join(msgs)
    return "empty", ""


def git_diff(base, cd):
    out = subprocess.run(["git", "diff", f"{base}...HEAD"], cwd=str(cd or Path.cwd()),
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.stdout


REVIEW_PROMPT = """Review this diff for defects. You are the independent reviewer:
the code was written by a different model, and your value is entirely in what it
missed.

For each finding give: severity (BLOCKER/HIGH/MEDIUM/LOW), the file:line, and a
concrete failure scenario - inputs or state that produce a wrong result. A
finding with no failure scenario is a style opinion; say so or drop it.

End with exactly one line: VERDICT: LGTM or VERDICT: CHANGES_REQUESTED.
If you could not read enough of the diff to judge, end with VERDICT: INCONCLUSIVE
rather than LGTM. Silence about what you could not see reads as approval, and
that is the failure mode that matters here.

--- diff ---
{diff}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file", help="path, or '-' for stdin")
    ap.add_argument("--tier", choices=("cheap", "work", "frontier"),
                    help="read model+effort from routes.v1.json")
    ap.add_argument("--model")
    ap.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max", "ultra"))
    ap.add_argument("--sandbox", default="read-only",
                    choices=("read-only", "workspace-write", "danger-full-access"),
                    help="default read-only: the router advises, it does not write")
    ap.add_argument("--cd", help="working root for the agent")
    ap.add_argument("--review", action="store_true", help="review a diff instead")
    ap.add_argument("--base", default="origin/staging", help="diff base for --review")
    ap.add_argument("--stall", type=float, default=STALL_S)
    ap.add_argument("--max", type=float, default=MAX_S)
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--no-ledger", action="store_true")
    a = ap.parse_args()

    if a.list_models:
        for m in load_models():
            if m.get("visibility") == "hide":
                continue
            levels = ",".join(lv.get("effort", "") for lv in m.get("supported_reasoning_levels", []))
            print(f"{m['slug']:<20} default={m.get('default_reasoning_level', '?'):<7} [{levels}]")
            print(f"{'':<20} {m.get('description', '')}")
        return 0

    model, effort = a.model, a.effort
    if a.tier:
        t_model, t_effort = tier_spec(a.tier)
        model = model or t_model
        effort = effort or t_effort
    if not model:
        ap.error("need --tier or --model")

    problem = validate(model, effort)
    if problem:
        print(f"refused: {problem}", file=sys.stderr)
        return 2

    cd = Path(a.cd) if a.cd else None
    if a.review:
        diff = git_diff(a.base, cd)
        if not diff.strip():
            print(f"refused: git diff {a.base}...HEAD is empty", file=sys.stderr)
            return 2
        prompt = REVIEW_PROMPT.format(diff=diff)
    elif a.prompt_file:
        prompt = sys.stdin.read() if a.prompt_file == "-" else Path(a.prompt_file).read_text(
            encoding="utf-8", errors="replace")
    elif a.prompt:
        prompt = a.prompt
    else:
        ap.error("need --prompt, --prompt-file or --review")

    work = Path(tempfile.mkdtemp(prefix="codex-task-"))
    jsonl, last_msg = work / "events.jsonl", work / "final.txt"
    args = build_args(model, effort, a.sandbox, cd, jsonl, last_msg)

    started = time.time()
    status, text = "empty", ""
    for i, label in enumerate(("codex", "codex-retry")):
        run_state = attempt(prompt, args, jsonl, last_msg, stall=a.stall, max_s=a.max,
                            label=label)
        status, text = final_message(last_msg, jsonl)
        if run_state == "killed" and status == "ok":
            # Inherited lesson: a killed run that still left a final message is
            # PARTIAL, never ok. Trusting it is how a truncated review passes.
            status = "partial"
        if status == "ok":
            break
        print(f"[{label}] {status}; {'retrying once' if i == 0 else 'giving up'}",
              file=sys.stderr)

    elapsed = time.time() - started
    if not a.no_ledger:
        try:
            import ledger
            ledger.append({
                "type": "dispatch", "fleet": "codex", "model": model, "effort": effort,
                "tier": a.tier, "project": Path(cd or Path.cwd()).name,
                "mode": "review" if a.review else "exec",
                "status": status, "seconds": round(elapsed, 1), "via": "codex_task",
                # Dollars are the wrong unit and writing 0.0 would be a lie of a
                # different kind: this is subscription QUOTA being spent.
                "cost_unit": "quota",
            })
        except Exception:
            pass

    if status == "empty":
        print(f"inconclusive: no final message after 2 attempts ({elapsed:.0f}s)",
              file=sys.stderr)
        print(f"events: {jsonl}", file=sys.stderr)
        return 12
    print(text)
    if status == "partial":
        print(f"[partial output after {elapsed:.0f}s - INCONCLUSIVE, not clean]",
              file=sys.stderr)
        return 12
    if a.review and "VERDICT: CHANGES_REQUESTED" in text:
        return 10
    if a.review and "VERDICT: LGTM" not in text:
        return 12
    return 0


if __name__ == "__main__":
    sys.exit(main())
