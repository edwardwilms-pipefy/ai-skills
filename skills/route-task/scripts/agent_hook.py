#!/usr/bin/env python3
"""PreToolUse(Agent) hook: record which subagent I ACTUALLY dispatched.

Wired by install.ps1 / install.sh as:
    python <abs path>/agent_hook.py     with matcher "Agent"

WHY THIS IS THE MOST VALUABLE LINE IN THE LEDGER

route_hook.py writes what the router RECOMMENDED. This writes what I DID. The
gap between them is the calibration signal the whole design rests on, and it
costs nothing to collect: when I ignore the advice and dispatch something else,
that disagreement is recorded without me saying a word. Asking me to rate the
router would have produced a signal I stopped supplying after a week.

It also feeds quota pressure: dispatch counts per fleet in the trailing window
are how the policy decides which subscription to spend next. Measuring what I
SPENT is not measuring what is LEFT - neither Claude Max nor Codex exposes
remaining quota - and every consumer of that number says so.

Like route_hook.py, this fails open and silent. A PreToolUse hook that errors
can block the tool call; blocking a dispatch to log a dispatch would be absurd.
It never emits a permission decision - it only observes.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MAX_STDIN_BYTES = 256 * 1024

# Which fleet a subagent name spends. The route-* agents are this router's own
# dispatch targets; everything else is a built-in, which also runs on Claude.
# Codex and Gemini dispatches are NOT visible here - they go through Bash, so
# codex_task.py and gemini_task.py write their own dispatch records. If this
# were the only writer, quota pressure would conclude Claude is the only fleet
# in use and would never switch away from it.
TIER_BY_AGENT = {
    "route-scout": "cheap",
    "route-builder": "work",
    "route-deep": "frontier",
}


def main() -> int:
    raw = sys.stdin.read(MAX_STDIN_BYTES) if not sys.stdin.isatty() else ""
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0

    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if tool and tool != "Agent":
        return 0

    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    agent = tool_input.get("subagent_type") or tool_input.get("subagentType") or ""

    import ledger
    ledger.append({
        "type": "dispatch",
        "session_id": payload.get("session_id") or payload.get("sessionId") or "",
        "project": Path(payload.get("cwd") or os.getcwd()).name,
        "fleet": "claude",
        "agent": agent,
        "tier": TIER_BY_AGENT.get(agent),
        # Routed by this router, or chosen freely? The route-* prefix is the only
        # honest way to tell, and the share of non-route-* dispatches is what
        # says whether these three agents are earning their place in the
        # always-resident agent list.
        "from_router": agent in TIER_BY_AGENT,
        "via": "agent_hook",
    })
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException:
        sys.exit(0)
