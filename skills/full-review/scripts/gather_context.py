#!/usr/bin/env python3
"""Phase 0 of /full-review: pin the snapshot and write the reviewer's evidence files.

Untrusted text (PR/issue bodies, comments, diffs) is written to files only; it is
never interpolated into a shell command.

    gather_context.py <pr> --worktree WT --run-dir RUN [--plan P] [--report R]
                      [--at SHA] [--as-of ISO8601] [--skill-dir DIR]

--at      review this commit instead of the live PR head (historical replay; implies
          review-only and skips the headRefOid check).
--as-of   drop PR/issue comments created after this instant and use the PR body
          version that was current then (for faithful replays).
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import shlex
import subprocess
import sys

CLOSE_RE = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)
TITLE_REF_RE = re.compile(r"\(#(\d+)\)")
MIGRATION_RE = re.compile(r"^(?P<dir>.*/migrations/)(?P<num>\d{4})_[^/]+\.py$")
DEPLOY_RE = re.compile(
    r"(^|/)(docker-compose[^/]*\.ya?ml|compose[^/]*\.ya?ml|gunicorn[^/]*\.(py|conf)|Procfile|"
    r"Dockerfile[^/]*|\.env\.example|crontab[^/]*|nginx[^/]*\.conf)$|(^|/)(k8s|helm)/"
)
WORKERS_RE = re.compile(r"(--workers|WEB_CONCURRENCY|GUNICORN_WORKERS|workers\s*[=:]|--threads|replicas\s*:)")


def run(args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"FAILED: {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def gh_json(args, cwd=None, check=True):
    out = run(["gh", *args], cwd, check)
    return json.loads(out) if out.strip() else None


def gh_paginate(path, cwd):
    """gh 2.45 has no --slurp: --paginate concatenates one JSON array per page."""
    out = run(["gh", "api", "--paginate", path], cwd)
    dec, i, items = json.JSONDecoder(), 0, []
    while i < len(out):
        while i < len(out) and out[i].isspace():
            i += 1
        if i >= len(out):
            break
        obj, i = dec.raw_decode(out, i)
        items.extend(obj if isinstance(obj, list) else [obj])
    return items


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pr", type=int)
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--plan")
    ap.add_argument("--report")
    ap.add_argument("--at")
    ap.add_argument("--as-of")
    ap.add_argument("--skill-dir", default=str(pathlib.Path(__file__).resolve().parent.parent))
    a = ap.parse_args()

    wt = pathlib.Path(a.worktree).resolve()
    rundir = pathlib.Path(a.run_dir).resolve()
    ctx = rundir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    rundir.chmod(0o700)
    as_of = ts(a.as_of) if a.as_of else None

    def keep(created):
        return as_of is None or ts(created) <= as_of

    repo = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], wt).strip()
    owner, name = repo.split("/")
    default_branch = run(["gh", "repo", "view", "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"], wt).strip()
    pr = gh_json(["pr", "view", str(a.pr), "--json",
                  "number,title,url,body,baseRefName,headRefName,headRefOid,author,isDraft,state,files"], wt)
    base_ref = pr["baseRefName"]
    run(["git", "-C", str(wt), "fetch", "-q", "origin", base_ref])

    head_sha = run(["git", "-C", str(wt), "rev-parse", a.at or "HEAD"]).strip()
    if not a.at and head_sha != pr["headRefOid"]:
        sys.exit(f"SNAPSHOT_MISMATCH: local HEAD {head_sha} != GitHub headRefOid {pr['headRefOid']}. "
                 "Push or pull so they match, then rerun.")
    base_sha = run(["git", "-C", str(wt), "rev-parse", f"origin/{base_ref}"]).strip()
    merge_base = run(["git", "-C", str(wt), "merge-base", head_sha, base_sha]).strip()

    # PR body as of --as-of (userContentEdits.diff holds the full body of each version).
    body = pr["body"] or ""
    if as_of:
        q = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
             "{userContentEdits(first:100){nodes{editedAt diff}}}}}")
        d = gh_json(["api", "graphql", "-F", f"o={owner}", "-F", f"r={name}", "-F", f"n={a.pr}", "-f", f"query={q}"], wt)
        edits = [e for e in d["data"]["repository"]["pullRequest"]["userContentEdits"]["nodes"]
                 if e.get("diff") is not None and ts(e["editedAt"]) <= as_of]
        if edits:
            body = max(edits, key=lambda e: ts(e["editedAt"]))["diff"]

    # Linked issues: GraphQL closing refs (gh 2.45 lacks the --json field), body and title refs.
    q = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
         "{closingIssuesReferences(first:20){nodes{number}}}}}")
    d = gh_json(["api", "graphql", "-F", f"o={owner}", "-F", f"r={name}", "-F", f"n={a.pr}", "-f", f"query={q}"], wt)
    cands = [n["number"] for n in d["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]]
    cands += [int(n) for n in CLOSE_RE.findall(body)]
    cands += [int(n) for n in CLOSE_RE.findall(pr["title"])] + [int(n) for n in TITLE_REF_RE.findall(pr["title"])]
    issues = []
    for n in dict.fromkeys(cands):
        if n == a.pr:
            continue
        it = gh_json(["api", f"repos/{repo}/issues/{n}"], wt, check=False)
        if it and "pull_request" not in it:
            issues.append(it)

    with open(ctx / "issues.md", "w") as f:
        if not issues:
            f.write("<no linked issues found: derive requirements from the PR description only>\n")
        for it in issues:
            labels = ", ".join(l["name"] for l in it.get("labels", []))
            f.write(f"## Issue #{it['number']}: {it['title']}\nURL: {it['html_url']}\nState: {it['state']}\n"
                    f"Labels: {labels}\nAuthor: {it['user']['login']} ({it.get('author_association')})\n\n"
                    f"{it.get('body') or '<empty>'}\n\n")
            comments = [c for c in gh_paginate(f"repos/{repo}/issues/{it['number']}/comments", wt) if keep(c["created_at"])]
            if comments:
                f.write(f"### Comments on #{it['number']} (oldest first; a later comment can supersede the body)\n\n")
                for c in comments:
                    f.write(f"#### {c['user']['login']} ({c.get('author_association')}) at {c['created_at']}\n"
                            f"{c.get('body') or ''}\n\n")

    pr_comments = [c for c in gh_paginate(f"repos/{repo}/issues/{a.pr}/comments", wt) if keep(c["created_at"])]
    changed = run(["git", "-C", str(wt), "diff", "--name-only", f"{merge_base}..{head_sha}", "--"]).split()
    with open(ctx / "pr.md", "w") as f:
        f.write(f"# PR #{pr['number']}: {pr['title']}\nURL: {pr['url']}\nAuthor: {pr['author']['login']}\n"
                f"Base: {base_ref}  Head: {pr['headRefName']}\n\n## PR Description (author-written: claims, not evidence)\n"
                f"{body or '<empty>'}\n\n## Changed Files\n" + "\n".join(changed) + "\n")
        if pr_comments:
            f.write("\n## PR Comments (author test evidence is AUTHOR-CLAIMED unless reproduced)\n\n")
            for c in pr_comments:
                f.write(f"#### {c['user']['login']} ({c.get('author_association')}) at {c['created_at']}\n{c.get('body') or ''}\n\n")
    (ctx / "changed-files.txt").write_text("\n".join(changed) + "\n")
    (ctx / "patch.diff").write_text(run(["git", "-C", str(wt), "diff", "--no-ext-diff", "--no-textconv",
                                         "--find-renames", f"{merge_base}..{head_sha}", "--"]))

    # Plan / report. A bare .pair/PLAN.md often belongs to another issue: use it only if it names a linked one.
    def names_issue(p):
        head = "\n".join(p.read_text(errors="replace").splitlines()[:80])
        return any(re.search(rf"#{it['number']}\b", head) for it in issues)

    def resolve(arg, stem, pr_comment_prefix=None):
        if arg:
            p = pathlib.Path(arg)
            return (p.read_text(), f"{arg} (--flag)") if p.is_file() else (None, f"{arg} not found")
        for it in issues:
            p = wt / ".pair" / f"{stem}-{it['number']}.md"
            if p.is_file():
                return p.read_text(), str(p.relative_to(wt))
        p = wt / ".pair" / f"{stem}.md"
        if p.is_file():
            if names_issue(p):
                return p.read_text(), str(p.relative_to(wt))
            skipped = f".pair/{stem}.md exists but names no linked issue: excluded as unrelated"
        else:
            skipped = "none"
        if pr_comment_prefix:
            hits = [c for c in pr_comments if (c.get("body") or "").startswith(pr_comment_prefix)]
            if hits:
                return hits[-1]["body"], f"PR comment {hits[-1]['html_url']}"
        return None, skipped

    plan, plan_src = resolve(a.plan, "PLAN")
    report, report_src = resolve(a.report, "REPORT", "## Implementation Report")
    (ctx / "plan.md").write_text(f"## Implementation Plan ({plan_src})\n\n{plan}\n" if plan else
                                 f"<no implementation plan ({plan_src}): GATE 3 (architecture) is N/A>\n")
    (ctx / "report.md").write_text(report if report else
                                   f"<no Implementation Report ({report_src}): no plan-adherence verification ran>\n")

    # Sibling PRs: shared files and same-numbered migrations (two leaves crash `migrate`; git never conflicts).
    added = run(["git", "-C", str(wt), "diff", "--name-only", "--diff-filter=A", f"{merge_base}..{head_sha}", "--"]).split()
    new_migs = [(m["dir"], m["num"], p) for p in added if (m := MIGRATION_RE.match(p))]
    open_prs = gh_json(["pr", "list", "--state", "open", "--limit", "100", "--json",
                        "number,title,headRefName,baseRefName,files"], wt) or []
    lines = []
    if base_ref != default_branch:
        lines.append(f"- STACKED PR: base is `{base_ref}`, not `{default_branch}`. Workflows filtered on "
                     f"`branches: [{default_branch}]` may not run at all: zero executed tests is missing CI, not green.")
    base_tree = run(["git", "-C", str(wt), "ls-tree", "-r", "--name-only", base_sha]).split()
    for d_, num, path in new_migs:
        clash = [p for p in base_tree if p.startswith(d_ + num + "_") and p != path]
        if clash:
            lines.append(f"- MIGRATION COLLISION with base `{base_ref}`: `{path}` vs `{', '.join(clash)}`")
    mine = set(changed)
    for o in open_prs:
        if o["number"] == a.pr:
            continue
        files = {f["path"] for f in o.get("files") or []}
        # CHANGELOG is shared by nearly every PR; it is noise here, not a collision signal.
        shared = sorted(p for p in mine & files if not re.search(r"(^|/)CHANGELOG[^/]*$", p))
        if shared:
            lines.append(f"- PR #{o['number']} ({o['title']}) also changes: {', '.join(shared[:10])}"
                         + (" ..." if len(shared) > 10 else ""))
        for d_, num, path in new_migs:
            hit = [p for p in files if p.startswith(d_ + num + "_") and p != path]
            if hit:
                lines.append(f"- MIGRATION COLLISION with open PR #{o['number']}: `{path}` vs `{', '.join(hit)}`")
    (ctx / "siblings.md").write_text("\n".join(lines) + "\n" if lines else "<no overlapping open PRs, no migration collisions>\n")

    # Deployment topology: worker/process counts decide whether in-process state is shared.
    deploy_files = [p for p in run(["git", "-C", str(wt), "ls-files"]).splitlines() if DEPLOY_RE.search(p)]
    out = ["Files:"] + [f"- {p}" for p in deploy_files[:60]] + ["", "Worker/concurrency settings found:"]
    for p in deploy_files:
        fp = wt / p
        if fp.is_file() and fp.stat().st_size < 400_000:
            for i, line in enumerate(fp.read_text(errors="replace").splitlines(), 1):
                if WORKERS_RE.search(line):
                    out.append(f"- {p}:{i}: {line.strip()[:160]}")
    (ctx / "deploy.md").write_text("\n".join(out) + "\n")

    hist = ctx / "history.md"
    if not hist.exists():
        hist.write_text("<none - this is iteration 1>\n")

    intent = ""
    m = re.search(r"^## Summary\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    if m:
        intent = next((l.strip() for l in m.group(1).splitlines() if l.strip()), "")
    snap = {
        "pr": a.pr, "repo": repo, "title": pr["title"], "intent": intent or pr["title"],
        "head_sha": head_sha, "base_sha": base_sha, "merge_base_sha": merge_base,
        "base_ref": base_ref, "head_ref": pr["headRefName"], "default_branch": default_branch,
        "author": pr["author"]["login"], "is_draft": pr["isDraft"], "state": pr["state"],
        "issues": [i["number"] for i in issues], "plan_source": plan_src if plan else None,
        "report_source": report_src if report else None, "replay_at": a.at, "as_of": a.as_of,
        "worktree": str(wt), "skill_dir": a.skill_dir,
    }
    (ctx / "snapshot.json").write_text(json.dumps(snap, indent=2) + "\n")
    env = {"PR_NUMBER": a.pr, "REPO": repo, "HEAD_SHA": head_sha, "BASE_SHA": base_sha,
           "MERGE_BASE_SHA": merge_base, "BASE_BRANCH": base_ref, "WT": wt, "RUN": rundir,
           "CTX": ctx, "PR_AUTHOR": pr["author"]["login"], "SKILL_DIR": a.skill_dir,
           "REVIEW_ONLY": "1" if a.at else ""}
    (rundir / "env.sh").write_text("".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in env.items()))

    print(json.dumps({k: snap[k] for k in ("repo", "pr", "head_sha", "base_sha", "merge_base_sha", "issues",
                                           "plan_source", "report_source")}, indent=2))
    print(f"changed files: {len(changed)}; issue comments + PR comments captured; siblings: "
          f"{len(lines)} note(s); env: {rundir / 'env.sh'}")


if __name__ == "__main__":
    main()
