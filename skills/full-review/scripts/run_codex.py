#!/usr/bin/env python3
"""Run one Codex review round with a stall watchdog and parse the verdict.

    run_codex.py --prompt RUN/prompt.md --cwd WT --out RUN/round-N [--stall 420] [--max 1800]

Uses `codex exec - --sandbox read-only --ephemeral --json` (prompt on stdin). Never
`codex exec review`: it rejects --sandbox, hangs silently, and has returned clean
reviews that dropped real findings.

Writes OUT/codex.jsonl, OUT/codex.err, OUT/review.md, OUT/result.json.
Exit: 0 LGTM (no blocking findings), 10 CHANGES_REQUESTED, 11 BLOCKED,
      12 INCONCLUSIVE (empty, partial, killed, or verdict/findings disagree).
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

FINDING_RE = re.compile(r"^#{2,4}\s*\[(BLOCKER|HIGH|MEDIUM|LOW|P1|P2|P3)\]\s*\[([A-Z]+)\]\s*(.*)$", re.M)
VERDICT_RE = re.compile(r"^\s*\**VERDICT:\s*(LGTM|CHANGES_REQUESTED|BLOCKED)\**\s*$", re.M)
LEGACY = {"P1": "BLOCKER", "P2": "HIGH", "P3": "MEDIUM"}


def attempt(prompt, cwd, out, stall, max_secs, label):
    jsonl, err = out / "codex.jsonl", out / "codex.err"
    cmd = ["codex", "exec", "-", "--sandbox", "read-only", "--ephemeral", "--json", "--cd", str(cwd)]
    with open(prompt) as fin, open(jsonl, "w") as fout, open(err, "w") as ferr:
        p = subprocess.Popen(cmd, stdin=fin, stdout=fout, stderr=ferr)
        start = last_change = time.time()
        last_size = 0
        while p.poll() is None:
            time.sleep(10)
            size = jsonl.stat().st_size
            if size > last_size:
                last_size, last_change = size, time.time()
            now = time.time()
            if now - last_change >= stall or now - start >= max_secs:
                print(f"[{label}] watchdog kill: stalled={now - last_change:.0f}s elapsed={now - start:.0f}s", file=sys.stderr)
                p.kill()
                p.wait()
                return "killed"
    return "exited"


def parse(out):
    msgs = []
    for line in (out / "codex.jsonl").read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        item = ev.get("item", {}) if ev.get("type") == "item.completed" else {}
        if item.get("type") == "agent_message" and item.get("text"):
            msgs.append(item["text"])
    if not msgs:
        return "empty", "NO_REVIEW_OUTPUT"
    final = [m for m in msgs if VERDICT_RE.search(m) or "AC Coverage Matrix" in m]
    if final:
        return "ok", final[-1]
    return "partial", "PARTIAL_REVIEW (no VERDICT line; codex likely hung before the final summary):\n\n" + "\n\n---\n\n".join(msgs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stall", type=int, default=420)  # codex can go >150s between events while reasoning
    ap.add_argument("--max", type=int, default=1800)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cwd = pathlib.Path(a.cwd)
    before = subprocess.run(["git", "-C", str(cwd), "status", "--porcelain"], capture_output=True, text=True).stdout

    status, text = None, None
    for i, label in enumerate(("codex", "codex-retry")):
        run_state = attempt(a.prompt, cwd, out, a.stall, a.max, label)
        status, text = parse(out)
        if run_state == "killed" and status == "ok" and not VERDICT_RE.search(text):
            status = "partial"
        if status == "ok":
            break
        print(f"[{label}] {status} output; {'retrying once' if i == 0 else 'giving up'}", file=sys.stderr)

    (out / "review.md").write_text(text + "\n")
    findings = [{"severity": LEGACY.get(s, s), "axis": ax, "title": t.strip()} for s, ax, t in FINDING_RE.findall(text)]
    blocking = [f for f in findings if f["severity"] in ("BLOCKER", "HIGH")]
    verdicts = VERDICT_RE.findall(text)
    verdict = verdicts[-1] if verdicts else None

    after = subprocess.run(["git", "-C", str(cwd), "status", "--porcelain"], capture_output=True, text=True).stdout
    worktree_touched = after != before

    if status != "ok" or verdict is None:
        outcome, code = "INCONCLUSIVE", 12
    elif verdict == "BLOCKED":
        outcome, code = "BLOCKED", 11
    elif verdict == "LGTM" and blocking:
        outcome, code = "INCONCLUSIVE", 12  # verdict line contradicts its own findings: never approve on it
    elif verdict == "LGTM":
        outcome, code = "LGTM", 0
    else:
        outcome, code = "CHANGES_REQUESTED", 10

    result = {"outcome": outcome, "verdict_line": verdict, "parse_status": status,
              "blocking": len(blocking), "findings": findings, "worktree_touched": worktree_touched,
              "stderr_tail": (out / "codex.err").read_text(errors="replace")[-1500:] if code == 12 else ""}
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "stderr_tail"}, indent=2))
    if worktree_touched:
        print("WARNING: the worktree changed during a read-only review; inspect `git status` before continuing", file=sys.stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()
