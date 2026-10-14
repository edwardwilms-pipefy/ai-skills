#!/usr/bin/env python3
"""CI gate for /full-review, bound to one exact commit.

    ci_gate.py --repo OWNER/NAME --sha SHA --base BRANCH [--wait 1800] [--grep-logs PATTERN ...]

Works on gh 2.45 (no `gh pr checks --json`): reads check runs for the SHA directly.

Rules:
- Required contexts come from rulesets (rules/branches/BASE) plus classic branch protection.
- A required context that is absent, pending, or SKIPPED is missing evidence -> CI_MISSING.
- Without required contexts: any executed failure -> CI_FAILED; anything still pending after
  --wait -> CI_MISSING; zero executed checks -> CI_MISSING (stacked base? conflict? path filter?).
- SKIPPED / NEUTRAL jobs are reported as not-run (N/A), never counted as passed.
- --grep-logs: for every executed job, fetch its log and report which patterns appear
  (prove the new regression test actually ran), plus test-count summary lines and
  `##[error]` lines inside successful jobs (soft-failing steps such as non-gating mypy).

Prints a JSON report; the last line is `CI_GREEN`, `CI_FAILED: ...` or `CI_MISSING: ...`.
Exit 0 / 1 / 2 respectively.
"""
import argparse
import json
import re
import subprocess
import sys
import time

FAIL = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
NOT_RUN = {"skipped", "neutral"}
SUMMARY_RE = re.compile(r"(=+ .*\b(passed|failed|error)\b.* =+|Tests:\s+.*\btotal\b|Ran \d+ tests? in|^\s*(OK|FAILED)( \(.*\))?$)")


def gh(args, check=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r


def paginate(path, key):
    out = gh(["api", "--paginate", path]).stdout
    dec, i, items = json.JSONDecoder(), 0, []
    while i < len(out):
        while i < len(out) and out[i].isspace():
            i += 1
        if i >= len(out):
            break
        obj, i = dec.raw_decode(out, i)
        items.extend(obj.get(key, []) if isinstance(obj, dict) else obj)
    return items


def required_contexts(repo, base):
    req = set()
    r = gh(["api", f"repos/{repo}/rules/branches/{base}"], check=False)
    if r.returncode == 0:
        for rule in json.loads(r.stdout or "[]"):
            if rule.get("type") == "required_status_checks":
                req |= {c["context"] for c in rule.get("parameters", {}).get("required_status_checks", [])}
    r = gh(["api", f"repos/{repo}/branches/{base}/protection/required_status_checks"], check=False)
    if r.returncode == 0:
        req |= set(json.loads(r.stdout).get("contexts", []))
    return req


def snapshot(repo, sha):
    runs = paginate(f"repos/{repo}/commits/{sha}/check-runs?per_page=100", "check_runs")
    checks = [{"name": c["name"], "id": c["id"], "status": c["status"], "conclusion": c.get("conclusion"),
               "url": c.get("html_url"), "app": (c.get("app") or {}).get("slug")} for c in runs]
    st = json.loads(gh(["api", f"repos/{repo}/commits/{sha}/status"]).stdout)
    for s in st.get("statuses", []):
        state = s["state"]
        checks.append({"name": s["context"], "id": None, "status": "completed" if state != "pending" else "in_progress",
                       "conclusion": {"success": "success", "failure": "failure", "error": "failure"}.get(state),
                       "url": s.get("target_url"), "app": "status"})
    return checks


def bucket(c):
    if c["status"] != "completed":
        return "pending"
    if c["conclusion"] in FAIL:
        return "fail"
    if c["conclusion"] in NOT_RUN:
        return "not_run"
    return "pass" if c["conclusion"] == "success" else "fail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--wait", type=int, default=1800)
    ap.add_argument("--grep-logs", nargs="*", default=[])
    a = ap.parse_args()

    deadline = time.time() + a.wait
    while True:
        checks = snapshot(a.repo, a.sha)
        if checks and not any(bucket(c) == "pending" for c in checks):
            break
        if time.time() >= deadline:
            break
        time.sleep(30)

    required = required_contexts(a.repo, a.base)
    by = {}
    for c in checks:
        by.setdefault(c["name"], []).append(c)
    groups = {"pass": [], "fail": [], "pending": [], "not_run": []}
    for c in checks:
        groups[bucket(c)].append(c["name"])

    if required:
        missing = [r for r in sorted(required) if r not in by or all(bucket(c) in ("pending", "not_run") for c in by[r])]
        fails = [r for r in sorted(required) if r in by and any(bucket(c) == "fail" for c in by[r])]
    else:
        missing = groups["pending"] + ([] if groups["pass"] or groups["fail"] else ["<no executed checks on this SHA>"])
        fails = groups["fail"]

    logs = {}  # always read executed job logs: test counts and soft failures need them
    for c in checks:
        if c["id"] is None or c["app"] != "github-actions" or bucket(c) not in ("pass", "fail"):
            continue
        r = gh(["api", f"repos/{a.repo}/actions/jobs/{c['id']}/logs"], check=False)
        if r.returncode:
            continue
        text = r.stdout
        info = {"summary": [l.split("Z ", 1)[-1].strip()[:200] for l in text.splitlines() if SUMMARY_RE.search(l.split("Z ", 1)[-1])][-4:]}
        if a.grep_logs:
            info["patterns"] = {p: bool(re.search(p, text)) for p in a.grep_logs}
        if bucket(c) == "pass":
            errs = [l.split("Z ", 1)[-1].strip()[:200] for l in text.splitlines() if "##[error]" in l]
            if errs:
                info["soft_fail_errors"] = errs[:5]
        logs[f"{c['name']} ({c['id']})"] = info

    report = {"sha": a.sha, "required_contexts": sorted(required) or "none configured (rulesets + classic protection)",
              "passed": groups["pass"], "failed": groups["fail"], "pending": groups["pending"],
              "not_run_skipped": groups["not_run"], "logs": logs}
    print(json.dumps(report, indent=2))
    if fails:
        print("CI_FAILED: " + ", ".join(fails))
        sys.exit(1)
    if missing:
        print("CI_MISSING: " + ", ".join(missing))
        sys.exit(2)
    print("CI_GREEN" + (f" (not run, not counted as passed: {', '.join(groups['not_run'])})" if groups["not_run"] else ""))


if __name__ == "__main__":
    main()
