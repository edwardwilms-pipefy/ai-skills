#!/usr/bin/env python3
"""Build the `state` the router sends to jev - and scrub it first.

    python state.py --prompt "fix the flaky test in the migration module"
    python state.py --prompt-file - --cwd /path/to/repo --json

WHY THIS FILE EXISTS, AND WHY IT IS NOT OPTIONAL

A UserPromptSubmit hook sees the raw prompt I type. Those prompts routinely carry
client names, card links, pipe ids, e-mail addresses and pasted private code.
jev is a third party - its own docs say it is "not affiliated with or endorsed by
TypeSafe AI" - so sending the raw prompt would publish client-adjacent material
outside the trust boundary on every single turn, invisibly.

So the state is BUILT, never forwarded:

  * the prompt is scrubbed and capped (this is the irreducible disclosure: the
    router cannot classify a task it is not told about);
  * repo signals are SHAPES, not names - stack, test runner, size bucket, dirty
    file count, branch KIND. "integrations-assistant" and "feat/route-task" never
    leave the machine; "python+typescript, large, 3 dirty, feat" does, and it is
    strictly more useful to the policy anyway;
  * pasted content is NEVER sent. Not a sample, not a hash of it, nothing.

HONEST LIMIT: this is a SCRUBBER, not a redactor. It removes what has a shape
(URLs, ids, paths, keys, addresses). It cannot remove a client's name typed as a
bare word - no regex can. The project allowlist in route.py is the real defense;
this file reduces exposure, it does not eliminate it. Verify with --json before
trusting it, and re-verify after editing any pattern here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Hard cap on the scrubbed task text. Two jobs at once: cost (you pay per input
# token) and exposure (what is not sent cannot leak). 600 chars ~ 150 tokens.
# ORIGIN OF THIS NUMBER: a declared guess, not a measurement. calibrate.py is
# what replaces it - run it at two caps and compare the confidence distribution.
# Until that is done, this comment is the only honest thing to say about 600.
MAX_TASK_CHARS = 600

# Ordered: earlier patterns win. URLs before paths, because a URL contains
# slashes that the path pattern would otherwise eat half of.
SCRUBBERS: tuple[tuple[re.Pattern[str], str], ...] = (
    # credentials first - they must never survive, whatever else matches
    (re.compile(r"jv_(?:live|test)_[A-Za-z0-9_\-]+"), "<key>"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "<key>"),
    (re.compile("AIza" + r"[A-Za-z0-9_\-]{20,}"), "<key>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "<key>"),
    (re.compile(r"(?:ya29|1//)[A-Za-z0-9_\-\.]{20,}"), "<key>"),
    (re.compile(r"(?i)\b(?:bearer|authorization|api[_-]?key|token|password|secret)\b"
                r"\s*[:=]?\s*\S+"), "<credential>"),
    # JWTs: three base64url segments. Caught before the generic blob rule.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "<jwt>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\bhttps?://\S+"), "<url>"),
    (re.compile(r"\b[A-Za-z]:[\\/][^\s\"'<>|]+"), "<path>"),       # C:\Users\...
    (re.compile(r"(?<![\w.])/(?:home|Users|mnt|opt|var|etc|srv)/[^\s\"'<>|]+"), "<path>"),
    # FILENAMES. Found by the --offline calibration pass, 2026-10-21, before a
    # single token was spent: the scrubber was shipping
    # "Fixture_Doc_V4_01012026 - Copy.html" and "fixture_page.html" - client
    # deliverable names - because they are RELATIVE paths and the absolute-path
    # rules above never saw them. Claude Code's own @-mention syntax made them
    # frequent. The extension is kept (it is real signal about the stack); the
    # name, which is the leak, is not.
    (re.compile(r"@?\"[^\"\n]{1,100}\.(\w{1,6})\""), r"<file.\1>"),
    (re.compile(r"@?'[^'\n]{1,100}\.(\w{1,6})'"), r"<file.\1>"),
    (re.compile(r"@[^\s\"'\n]{1,100}\.(\w{1,6})"), r"<file.\1>"),
    # The (?<!<) stops this rule from re-matching INSIDE a placeholder the two
    # rules above just wrote: without it, @"x.html" became <<file.html>>, because
    # "file.html" is itself a bare filename. Substitutions are sequential, so any
    # later pattern can eat an earlier replacement.
    (re.compile(r"(?<![<\w.-])[\w\-]{1,60}\.(html?|pdf|docx?|xlsx?|pptx?|csv|png|jpe?g"
                r"|gif|svg|zip|py|tsx?|jsx?|json|md|ya?ml|sql|tf|go|rs|java|rb|sh|ps1"
                r"|bat|toml|ini|cfg|txt|log|xml|env)\b", re.I), r"<file.\1>"),
    # QUOTED CONTENT. Same pass, same reason: 'uma linha longa de conteudo citado'
    # and 'campo de formulario' are lines out of a client's insurance document,
    # quoted verbatim into a prompt. Long quoted runs are content; short ones are
    # usually identifiers ('main', 'sonnet', 'pytest') and carry classification
    # signal, so they survive.
    # The lookarounds on the single-quote rule matter: an apostrophe in ordinary
    # prose ("it's", "doesn't") pairs with the next real quote and swallowed
    # unrelated text - measured output was "'copy' is one that i<quoted>s".
    # A quote that opens a span is preceded by a non-word character.
    # 15 is a judgement call, not a measurement: it trades a little
    # classification signal for not shipping client prose.
    (re.compile(r"\"[^\"\n]{15,}\""), "<quoted>"),
    (re.compile(r"(?<![\w])'[^'\n]{15,}'(?![\w])"), "<quoted>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{40}\b"), "<sha>"),
    # Pipefy card/pipe/org ids BEFORE short shas: an all-digit run is an id in
    # practice, and "1109887766" also matches the short-sha pattern. Measured
    # 2026-10-21 with the sha rule first: card and org ids came out labelled
    # <sha>. Both labels scrub equally, so privacy held - but the classifier was
    # being told "sha" about a card id, and --show-state read wrong to a human.
    (re.compile(r"\b\d{6,}\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{7,12}\b(?=\s|$|[.,;)])"), "<sha>"),   # short git shas
    # Long opaque runs: base64 blobs, minified payloads. After the specific rules
    # so a real key is labelled <key> rather than <blob>.
    (re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"), "<blob>"),
)

# How deep to look for stack/test markers. 3 covers the layouts that matter
# (backend/, frontend/, apps/<x>/, packages/<x>/) without turning into a walk.
MARKER_DEPTH = 3

# Markers -> stack name. First match per family wins; several families can hold.
STACK_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python", ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile")),
    ("node", ("package.json",)),
    ("go", ("go.mod",)),
    ("rust", ("Cargo.toml",)),
    ("ruby", ("Gemfile",)),
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts")),
    ("dotnet", ("global.json",)),
    ("terraform", ("main.tf",)),
    ("docker", ("Dockerfile", "docker-compose.yml")),
)

TEST_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pytest", ("pytest.ini", "conftest.py", "tox.ini")),
    ("vitest-or-jest", ("vitest.config.ts", "vitest.config.js", "jest.config.js")),
    ("go-test", ("go.mod",)),
    ("cargo-test", ("Cargo.toml",)),
)

# Branch kinds we report. Anything else becomes "other" - a branch named after a
# client or a ticket title must not be the thing we ship off the machine.
BRANCH_KINDS = ("main", "master", "staging", "production", "develop",
                "feat", "fix", "hotfix", "chore", "docs", "release", "revert")


def scrub(text: str) -> str:
    """Remove everything with a leakable shape. See the honest limit in __doc__."""
    for pattern, replacement in SCRUBBERS:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def cap(text: str, limit: int = MAX_TASK_CHARS) -> str:
    """Trim to `limit`, marking the cut so the classifier knows it saw a fragment."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ...[trimmed]"


def _git(args: list[str], cwd: Path) -> str:
    """Run a read-only git command. Empty string on any failure - not a git repo,
    git missing, or a timeout are all 'no signal', never an error that propagates."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def branch_kind(branch: str) -> str:
    """'feat/route-task' -> 'feat'. The slug after the slash is a feature NAME and
    is deliberately dropped: it is the part that leaks."""
    if not branch:
        return "unknown"
    head = branch.split("/", 1)[0].lower()
    return head if head in BRANCH_KINDS else "other"


def size_bucket(n_files: int) -> str:
    if n_files == 0:
        return "unknown"
    if n_files < 50:
        return "tiny"
    if n_files < 300:
        return "small"
    if n_files < 1500:
        return "medium"
    return "large"


def repo_signals(cwd: Path | None = None) -> dict:
    """Cheap, name-free signals about where the work is happening.

    These carry real information for the policy ("is this verifiable?" depends on
    whether a test runner exists) while naming nothing: no repo name, no branch
    slug, no paths.
    """
    cwd = Path(cwd or os.getcwd())
    root_out = _git(["rev-parse", "--show-toplevel"], cwd)
    root = Path(root_out) if root_out else cwd

    tracked = _git(["ls-files"], root)
    files = tracked.splitlines() if tracked else []
    n_files = len(files)
    dirty = _git(["status", "--porcelain"], root)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)

    # Markers are found at ANY depth up to MARKER_DEPTH, not just at the root.
    # MEASURED 2026-10-21: scanning only the root reported
    # {"stack": "docker", "test_runner": "none"} for a monorepo with 3,274 tests,
    # because its pyproject.toml, package.json and conftest.py live under
    # backend/ and frontend/. "no test runner" blocks every downgrade, so a
    # root-only scan would have permanently pinned that repo to expensive
    # routes - a wrong signal that fails costly and silently.
    # git ls-files is already fetched, respects .gitignore, and costs nothing.
    present = {
        part
        for path in files
        for part in [path.rsplit("/", 1)[-1]]
        if path.count("/") < MARKER_DEPTH
    }
    if root.is_dir():  # untracked-but-present markers still count
        present |= {p.name for p in root.iterdir()}
    stacks = [name for name, markers in STACK_MARKERS if present & set(markers)]
    runners = [name for name, markers in TEST_MARKERS if present & set(markers)]

    return {
        "stack": "+".join(stacks) or "unknown",
        "test_runner": runners[0] if runners else "none",
        "size": size_bucket(n_files),
        "dirty_files": len(dirty.splitlines()) if dirty else 0,
        "branch_kind": branch_kind(branch),
        "in_git_repo": bool(root_out),
    }


def build(
    prompt: str,
    *,
    cwd: Path | None = None,
    recent_tools: list[str] | None = None,
    include_repo: bool = True,
) -> dict:
    """The full state object sent to jev. Nothing else is ever sent."""
    state: dict = {"task": cap(scrub(prompt))}
    if include_repo:
        state["repo"] = repo_signals(cwd)
    if recent_tools:
        # Tool NAMES only - never their arguments, which carry paths and content.
        state["recent_tools"] = [t for t in recent_tools if re.fullmatch(r"\w{1,32}", t)][:8]
    return state


# --- selftest --------------------------------------------------------------
# This is the privacy gate expressed as a test instead of as prose in a doc.
# A prose promise fails open; this fails loud.

NASTY = (
    "fix the webhook on https://app.pipefy.com/pipes/304512783 for "
    "maria.silva@acme-cliente.com.br — card 1109887766 keeps failing. "
    r"log at C:\Users\edward.wilms\Documents\Clientes\Acme\dump.txt and "
    "/home/deploy/app/.env has JEV_API_KEY=jv_live_Zq91xKpL77aabbccddee plus "
    "api_key: " + "AIza" + "Sy" + "D-" + "9" * 31 + " and token "
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abc123signature — "
    "org 302372204, sha 724b595f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f, "
    "uuid acd84a65-95be-4713-92b1-2ac314d41ecf. "
    # The two cases the --offline calibration pass caught on real history.
    'check @"Fixture_Doc_V4_01012026 - Copy.html" and @fixture_page.html, '
    "and add a line below 'uma linha longa de conteudo citado' in fixture_report.docx"
)

MUST_NOT_SURVIVE = (
    "app.pipefy.com", "304512783", "maria.silva", "acme-cliente",
    "1109887766", "edward.wilms", "Clientes", "Acme", "dump.txt",
    "deploy", "jv_live_Zq91xKpL77", "AIza" + "SyD-99", "eyJhbGciOiJIUzI1NiJ9",
    "302372204", "724b595f1c2d", "acd84a65-95be",
    # filenames and quoted client content
    "Fixture_Doc", "Copia", "fixture_page", "fixture_report",
    "uma linha longa de conteudo",
)


def _selftest() -> int:
    failures = []

    def check(label: str, ok: bool, got: object = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {label}" + (f"  <- {got!r}" if not ok else ""))
        if not ok:
            failures.append(label)

    cleaned = scrub(NASTY)
    print(f"\nscrubbed:\n  {cleaned}\n")
    for needle in MUST_NOT_SURVIVE:
        check(f"removed {needle!r}", needle not in cleaned)

    # The scrub must still leave something classifiable, or the router is blind.
    for kept in ("fix", "webhook", "failing"):
        check(f"kept the task verb/noun {kept!r}", kept in cleaned.lower())

    # Regressions produced by the scrubber's own substitutions, both measured on
    # real history 2026-10-21. They are cheap to reintroduce by editing a pattern
    # and invisible without an assertion, so they live here permanently.
    check("no placeholder is nested inside another", "<<" not in cleaned, cleaned)
    apostrophes = scrub("the 'copy' is one that i think it's what i wanted")
    check("an apostrophe in prose does not open a quoted span",
          "<quoted>" not in apostrophes, apostrophes)
    check("a short quoted identifier survives (it is signal, not content)",
          "'copy'" in apostrophes, apostrophes)

    check("cap marks the cut", cap("y" * (MAX_TASK_CHARS + 50)).endswith("...[trimmed]"))
    check("cap respects the limit", len(cap("y" * 5000)) <= MAX_TASK_CHARS + 16)

    sig = repo_signals(Path(__file__).resolve().parent)
    print(f"\nrepo signals here: {json.dumps(sig)}")
    check("signals name nothing", not any(
        isinstance(v, str) and ("/" in v or "\\" in v or "ai-skills" in v)
        for v in sig.values()
    ), sig)

    st = build("do the thing", recent_tools=["Bash", "Edit", "rm -rf /"])
    check("tool args are rejected, only names pass", st["recent_tools"] == ["Bash", "Edit"],
          st["recent_tools"])
    check("pasted content has no path into the state", "pasted" not in json.dumps(st).lower())

    print(f"\n{len(failures)} failure(s)" if failures else "\nall checks passed")
    return 1 if failures else 0


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file", help="path, or '-' for stdin")
    ap.add_argument("--cwd")
    ap.add_argument("--no-repo", action="store_true", help="omit repo signals")
    ap.add_argument("--json", action="store_true", help="print the exact outbound payload")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()
    if a.prompt_file:
        text = sys.stdin.read() if a.prompt_file == "-" else Path(a.prompt_file).read_text(
            encoding="utf-8", errors="replace")
    elif a.prompt:
        text = a.prompt
    else:
        ap.error("need --prompt, --prompt-file or --selftest")

    state = build(text, cwd=Path(a.cwd) if a.cwd else None, include_repo=not a.no_repo)
    print(json.dumps(state, indent=2, ensure_ascii=False) if a.json
          else json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
