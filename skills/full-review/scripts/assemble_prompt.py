#!/usr/bin/env python3
"""Fill review-prompt.md from the context files written by gather_context.py.

    assemble_prompt.py --run-dir RUN [--template PATH] > RUN/prompt.md

Single-pass substitution: a {{KEY}} that appears inside PR or issue text is never expanded.
"""
import argparse
import json
import pathlib
import re
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--run-dir", required=True)
ap.add_argument("--template", default=str(pathlib.Path(__file__).resolve().parent.parent / "review-prompt.md"))
a = ap.parse_args()

ctx = pathlib.Path(a.run_dir) / "context"
snap = json.loads((ctx / "snapshot.json").read_text())
read = lambda n: (ctx / n).read_text().rstrip("\n")
values = {
    "PR_NUMBER": str(snap["pr"]), "PR_TITLE": snap["title"], "PR_INTENT": snap["intent"],
    "HEAD_SHA": snap["head_sha"], "BASE_SHA": snap["base_sha"], "MERGE_BASE_SHA": snap["merge_base_sha"],
    "BASE_BRANCH": snap["base_ref"], "PATCH_PATH": str(ctx / "patch.diff"),
    "PR_CONTEXT": read("pr.md"), "ISSUE_CONTEXT": read("issues.md"), "PLAN_CONTEXT": read("plan.md"),
    "REPORT_CONTEXT": read("report.md"), "SIBLINGS": read("siblings.md"), "DEPLOY": read("deploy.md"),
    "HISTORY": read("history.md"),
}
tpl = pathlib.Path(a.template).read_text()
missing = sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", tpl)) - values.keys())
if missing:
    sys.exit(f"template placeholders without values: {missing}")
sys.stdout.write(re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: values[m.group(1)], tpl))
