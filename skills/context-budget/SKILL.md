---
name: context-budget
allowed-tools: Bash(ls:*), Bash(wc:*), Bash(find:*), Bash(cat:*), Bash(head:*), Bash(grep:*), Bash(du:*), Read
argument-hint: [--verbose] (optional - adds per-file breakdown)
description: Audit what is consuming this session's context window - commands, skills, plugins, memory, MCP tool schemas, and the CLAUDE.md chain - and rank the reclaimable savings
---

<!--
  Source: affaan-m/ECC (github.com/affaan-m/ecc), skills/context-budget/SKILL.md
  Taken from npm ecc-universal@2.2.1, 2026-09-22. MIT licensed.
  Adapted by hand: ECC's Phase 1 scanned agents/, rules/ and .mcp.json, none of
  which exist in this setup. Phase 1 below is repointed at the real paths on this
  machine. ECC itself is NOT installed - no plugin, no hooks, no marketplace entry.
-->

# Context Budget

Estimate token overhead across every component loaded into a Claude Code session here, and
surface the optimizations worth making.

**This is a method, not a program.** There is no script behind it. Running `/context-budget`
means *you* do the counting with the commands below and fill in the report. Budget a few
minutes.

## When to Use

- Context fills up faster than the work justifies
- After adding MCP servers, plugins, commands, or memory files
- Before adding more, to find out whether there is room
- Output quality degrades late in long sessions

## Phase 1: Inventory

Count each of these. Token estimate: **prose = words × 1.3**, **code-heavy = chars / 4**.

### Commands and hand-written skills — `~/.claude/skills/*/SKILL.md`, `~/.claude/commands/*.md`

```bash
wc -lw ~/.claude/skills/*/SKILL.md ~/.claude/commands/*.md 2>/dev/null | sort -rn
```

Only the `description:` frontmatter line of each command is always resident; the body loads
when the command runs. Count both, and report them separately — they are very different costs.

- Flag: `description` over 30 words (always-resident bloat)
- Flag: body over 400 lines (expensive on invocation)
- Flag: any `.bak` / `.bak-preECC` file — dead weight, and confusing at a glance

### Skills — user, project, and plugin

```bash
ls ~/.claude/skills/ 2>/dev/null
find ~/.claude/plugins/cache -name "SKILL.md" | head -50
ls .claude/skills/ 2>/dev/null
```

Every available skill's `name` + `description` is resident in the system prompt whether or
not it is invoked. That listing is the real cost; the SKILL.md body is only paid on load.

- Flag: plugins contributing many skills you never invoke
- Flag: near-duplicate descriptions across skills (the model cannot pick between them either)

### Memory — the index is the resident part

```bash
# Claude Code keys project memory by the repo path with "/" replaced by "-"
MEM=~/.claude/projects/$( { git rev-parse --show-toplevel 2>/dev/null || pwd; } | sed 's#/#-#g')/memory
wc -c "$MEM/MEMORY.md"
ls "$MEM" | wc -l
awk 'length > 200' "$MEM/MEMORY.md" | head
```

`MEMORY.md` loads in full every session; the individual memory files do not. So the index
line count and line *length* is what costs, not the number of memories.

- Flag: `MEMORY.md` at or over its size limit — it truncates silently, losing the tail
- Flag: index lines over ~200 chars — the documented cause of that overflow

### MCP servers — big only when eagerly loaded

Count the tools each connected server exposes, then estimate **~500 tokens of JSON schema
per tool**. Deferred tools cost only their name until loaded; eagerly-loaded tools cost the
full schema every turn. Check which is which before blaming a server.

- Flag: any server over 20 tools
- Flag: servers that wrap a CLI already available through Bash (`gh`, `git`, `npm`, `docker`)
- Flag: more than 10 connected servers

### The CLAUDE.md chain

```bash
wc -lw ~/.claude/CLAUDE.md ~/.claude/RTK.md \
       "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/CLAUDE.md"
```

Every file in the chain is resident every turn, including `@`-imports.

- Flag: combined total over 300 lines
- Flag: content that duplicates what `MEMORY.md` already says

## Phase 2: Classify

| Bucket | Criteria | Action |
|---|---|---|
| **Always needed** | Referenced by CLAUDE.md, backs a command you actually run, matches this stack | Keep |
| **Sometimes needed** | Domain-specific, not referenced anywhere | Make on-demand / deferred |
| **Rarely needed** | No reference, overlaps another component, or does not match this stack at all | Remove |

Be concrete about "actually run" — if you cannot remember invoking it, it is not Always.

## Phase 3: Detect

- **MCP schemas, but only the eager ones.** A 30-tool server would outweigh every command and skill combined *if loaded eagerly* — deferred, it costs ~10 tokens per name. Check which before blaming it. Measured here 2026-09-22: 102 tools across 8 servers cost ~2,300 tokens, not ~51,000, because all but three are deferred.
- **Resident vs. on-load confusion** — optimizing a 400-line command body saves nothing per turn if only its description is resident. Fix the description first.
- **Redundant components** — a skill restating CLAUDE.md, a memory file restating the repo's own docs.
- **Index bloat** — long `MEMORY.md` pointer lines, which cost every turn and cause truncation.
- **Stale artifacts** — `.bak` files, memories describing code that no longer exists.

## Phase 4: Report

```
Context Budget Report
═══════════════════════════════════════
Model / window:        <model>, <N>K
Estimated overhead:    ~XX,XXX tokens (XX% of window)

┌──────────────────────┬───────┬──────────┬──────────┐
│ Component            │ Count │ Resident │ On-load  │
├──────────────────────┼───────┼──────────┼──────────┤
│ Commands             │ N     │ ~X,XXX   │ ~XX,XXX  │
│ Skills (incl plugin) │ N     │ ~X,XXX   │ ~XX,XXX  │
│ MEMORY.md index      │ 1     │ ~X,XXX   │ —        │
│ MCP tool schemas     │ N     │ ~XX,XXX  │ —        │
│ CLAUDE.md chain      │ N     │ ~X,XXX   │ —        │
└──────────────────────┴───────┴──────────┴──────────┘

Issues (N), ranked by reclaimable tokens:
1. [what] → ~X,XXX tokens
2. ...

Potential savings: ~XX,XXX tokens (XX% of overhead)
```

With `--verbose`, add per-file counts, the heaviest files line-by-line, and a per-server MCP
tool list with schema estimates.

## Rules of thumb

- **Find the resident set first, then rank.** Do not assume MCP dominates: deferral can cut it 20x, and the real top cost is often a long CLAUDE.md or memory index. Measured here, the order was CLAUDE.md chain > MEMORY.md index > skill descriptions > MCP.
- **Separate resident from on-load** in every number you report. Conflating them produces confident, useless recommendations.
- **Do not recommend deleting memories to fix `MEMORY.md` overflow.** The overflow is caused by long index lines. Shorten the pointers; the memory files themselves are not resident and cost nothing per turn.
- **Estimate, then say it is an estimate.** These are heuristics, not measurements.
- Re-run after adding any server, plugin, or command, to catch creep while it is still small.
