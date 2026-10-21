#!/usr/bin/env python3
"""The route ledger: what was recommended, what I actually dispatched, what it cost.

    python ledger.py --tail 20
    python ledger.py --shares            # dispatch share per fleet, trailing window
    python ledger.py --path              # where the ledger lives

WHERE IT LIVES, AND WHY NOT IN THE REPO

~/.claude/route-ledger.jsonl - OUTSIDE this repository, always. ai-skills is a
PUBLIC repo, and the ledger holds (scrubbed) prompts, project names and timing
from client work. .gitignore is the second line of defense; living somewhere git
never looks is the first. Override with $ROUTE_LEDGER for tests.

THREE RECORD TYPES, JOINED BY session_id

  decision  - the router's recommendation, written by route_hook.py per turn
  dispatch  - what I ACTUALLY ran, written by agent_hook.py on PreToolUse(Agent)
  judgement - did the output serve, written by judge.py

The gap between `decision` and `dispatch` is the calibration signal this whole
system is built on, and it is free: I disagree with the router by doing something
else, and that disagreement is recorded without me saying a word.

APPEND NEVER RAISES BY DEFAULT. A hook that dies because the ledger is unwritable
would cost a turn to save a log line. Call append(strict=True) from a CLI, where
a silent failure is the worse outcome.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECORD_TYPES = ("decision", "dispatch", "judgement")


def ledger_path() -> Path:
    override = os.environ.get("ROUTE_LEDGER")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "route-ledger.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append(record: dict, *, strict: bool = False) -> bool:
    """Write one record. Returns True on success.

    `ts` and `type` are stamped here so no caller can forget them; a record
    without a type cannot be joined and is therefore invisible to the report,
    which is worse than being absent.
    """
    if record.get("type") not in RECORD_TYPES:
        raise ValueError(f"type must be one of {RECORD_TYPES}, got {record.get('type')!r}")
    record = {"ts": now_iso(), **record}
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        if strict:
            raise
        return False


def read(*, since: datetime | None = None, types: tuple[str, ...] | None = None) -> list[dict]:
    """Read records, skipping malformed lines.

    A half-written line (killed mid-append) must not make the whole ledger
    unreadable - the report would then silently cover less than it claims.
    """
    path = ledger_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if types and rec.get("type") not in types:
            continue
        if since is not None:
            ts = parse_ts(rec.get("ts"))
            if ts is None or ts < since:
                continue
        out.append(rec)
    return out


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def dispatch_shares(window_hours: float = 5.0) -> dict[str, float]:
    """Share of dispatches per fleet in the trailing window. Empty dict if none.

    HONEST LIMIT, and it is the important part: neither Claude Max nor Codex
    exposes remaining quota through any API. This measures what I SPENT, not what
    is LEFT. It is a proxy for quota pressure, and every caller that prints it
    must say so - a number labelled like a gauge, that is not a gauge, is worse
    than no number.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    recs = read(since=since, types=("dispatch",))
    if not recs:
        return {}
    counts: dict[str, int] = {}
    for rec in recs:
        fleet = rec.get("fleet")
        if isinstance(fleet, str) and fleet:
            counts[fleet] = counts.get(fleet, 0) + 1
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()} if total else {}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tail", type=int, metavar="N", help="print the last N records")
    ap.add_argument("--shares", action="store_true", help="dispatch share per fleet")
    ap.add_argument("--window", type=float, default=5.0, help="window in hours for --shares")
    ap.add_argument("--path", action="store_true")
    ap.add_argument("--type", choices=RECORD_TYPES, help="filter --tail by type")
    a = ap.parse_args()

    if a.path:
        print(ledger_path())
        return 0
    if a.shares:
        shares = dispatch_shares(a.window)
        if not shares:
            print(f"no dispatches in the last {a.window}h (proxy for spend, NOT remaining quota)")
            return 0
        for fleet, share in sorted(shares.items(), key=lambda kv: -kv[1]):
            print(f"{fleet:<8} {share:6.1%}")
        print("\n(share of what I SPENT; neither subscription exposes what is LEFT)")
        return 0
    if a.tail:
        recs = read(types=(a.type,) if a.type else None)
        if not recs:
            print(f"ledger is empty: {ledger_path()}")
            return 0
        for rec in recs[-a.tail:]:
            print(json.dumps(rec, ensure_ascii=False))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
