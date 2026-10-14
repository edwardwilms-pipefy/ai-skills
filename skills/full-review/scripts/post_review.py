#!/usr/bin/env python3
"""Publish the /full-review result to the PR, idempotently, bound to the reviewed head.

    post_review.py --repo OWNER/NAME --pr N --head SHA --outcome OUTCOME --body-file FILE

1. Re-reads the PR. If headRefOid moved, publishes nothing (exit 3): the review is stale.
2. Upserts ONE audit comment per (PR, head) carrying the marker
   <!-- full-review:PR:HEAD:OUTCOME -->; a rerun on the same head edits it instead of posting twice.
3. Submits a formal review anchored to HEAD only when the authenticated actor is not the PR
   author (GitHub rejects APPROVE/REQUEST_CHANGES on your own PR) and the PR is open and not draft:
   APPROVED -> APPROVE; CHANGES_REQUESTED/BLOCKED/MAX_ITERATIONS_REACHED/CI_FAILED -> REQUEST_CHANGES;
   anything else -> COMMENT. Skips if this actor already left that state on this commit.
4. Re-reads GitHub to verify, prints URLs and the repository reviewDecision. Never merges.
Exit 0 published+verified, 3 stale head, 4 publication or verification failed.
"""
import argparse
import json
import pathlib
import subprocess
import sys

REQUEST_CHANGES = {"CHANGES_REQUESTED", "BLOCKED", "MAX_ITERATIONS_REACHED", "CI_FAILED"}


def gh(args, payload=None, check=True):
    r = subprocess.run(["gh", *args], input=json.dumps(payload) if payload is not None else None,
                       capture_output=True, text=True)
    if check and r.returncode:
        print(f"gh {' '.join(args)} failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(4)
    return json.loads(r.stdout) if r.stdout.strip() else None


def paginate(path):
    out = subprocess.run(["gh", "api", "--paginate", path], capture_output=True, text=True, check=True).stdout
    dec, i, items = json.JSONDecoder(), 0, []
    while i < len(out):
        while i < len(out) and out[i].isspace():
            i += 1
        if i >= len(out):
            break
        obj, i = dec.raw_decode(out, i)
        items.extend(obj)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--outcome", required=True)
    ap.add_argument("--body-file", required=True)
    a = ap.parse_args()

    body = pathlib.Path(a.body_file).read_text().strip()
    if len(body) < 40:
        print("refusing to publish an empty or near-empty body", file=sys.stderr)
        sys.exit(4)
    prefix = f"<!-- full-review:{a.pr}:{a.head}:"
    marker = f"{prefix}{a.outcome} -->"
    body = marker + "\n" + body.replace(marker, "").strip() + "\n"

    pr = gh(["pr", "view", str(a.pr), "--repo", a.repo, "--json", "headRefOid,author,state,isDraft,url"])
    if pr["headRefOid"] != a.head:
        print(f"STALE: PR head is {pr['headRefOid']}, review was for {a.head}. Nothing published.", file=sys.stderr)
        sys.exit(3)
    actor = gh(["api", "user"])["login"]

    mine = [c for c in paginate(f"repos/{a.repo}/issues/{a.pr}/comments")
            if c["user"]["login"] == actor and prefix in (c.get("body") or "")]
    if mine:
        c = gh(["api", "-X", "PATCH", f"repos/{a.repo}/issues/comments/{mine[-1]['id']}", "--input", "-"], {"body": body})
    else:
        c = gh(["api", "-X", "POST", f"repos/{a.repo}/issues/{a.pr}/comments", "--input", "-"], {"body": body})
    comment_url, comment_id = c["html_url"], c["id"]

    review_note = "skipped: actor is the PR author (GitHub rejects self-review); the audit comment is the record"
    if actor != pr["author"]["login"]:
        if pr["state"] != "OPEN" or pr["isDraft"]:
            review_note = f"skipped: PR is {pr['state'].lower()}{' draft' if pr['isDraft'] else ''}"
        else:
            event = "APPROVE" if a.outcome == "APPROVED" else "REQUEST_CHANGES" if a.outcome in REQUEST_CHANGES else "COMMENT"
            state = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}[event]
            existing = [r for r in paginate(f"repos/{a.repo}/pulls/{a.pr}/reviews")
                        if r["user"]["login"] == actor and r.get("commit_id") == a.head and r["state"] == state]
            if existing:
                review_note = f"reused existing {state} review {existing[-1]['html_url']}"
            else:
                short = f"{marker}\nFull review: **{a.outcome}** at `{a.head}`. Details: {comment_url}\nThis workflow did not merge."
                r = gh(["api", "-X", "POST", f"repos/{a.repo}/pulls/{a.pr}/reviews", "--input", "-"],
                       {"commit_id": a.head, "event": event, "body": short})
                if r.get("state") != state or r.get("commit_id") != a.head:
                    print(f"review verification failed: {r.get('state')} @ {r.get('commit_id')}", file=sys.stderr)
                    sys.exit(4)
                review_note = f"{state} review {r['html_url']}"

    check = gh(["api", f"repos/{a.repo}/issues/comments/{comment_id}"])
    if marker not in check.get("body", ""):
        print("audit comment verification failed", file=sys.stderr)
        sys.exit(4)
    decision = gh(["pr", "view", str(a.pr), "--repo", a.repo, "--json", "reviewDecision"]).get("reviewDecision")
    print(json.dumps({"audit_comment": comment_url, "formal_review": review_note,
                      "review_decision": decision or "none", "merged": False}, indent=2))


if __name__ == "__main__":
    main()
