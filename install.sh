#!/usr/bin/env bash
# Symlink skills/<name>/ into each agent's skills directory.
#
#   ./install.sh                      # Claude Code only (default)
#   ./install.sh --codex              # Codex only
#   ./install.sh --claude --codex     # both
#   ./install.sh --upstream-codex     # also link lhermoso's gh-workflow-suite into ~/.codex/skills
#   ./install.sh --dry-run ...        # print what would change
#   ./install.sh --uninstall ...      # remove only symlinks that point into this repo
#
# Adding a new agent: add one line to TARGETS below. Every agent that reads the
# open Agent Skills layout (<dir>/<name>/SKILL.md) works the same way.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
declare -A TARGETS=(
  [claude]="$HOME/.claude/skills"
  [codex]="$HOME/.codex/skills"
)

selected=() dry=0 uninstall=0 upstream_codex=0
for arg in "$@"; do
  case "$arg" in
    --claude) selected+=(claude) ;;
    --codex) selected+=(codex) ;;
    --upstream-codex) upstream_codex=1 ;;
    --dry-run) dry=1 ;;
    --uninstall) uninstall=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
[ ${#selected[@]} -eq 0 ] && selected=(claude)

run() { if [ "$dry" = 1 ]; then echo "+ $*"; else "$@"; fi; }

link_one() {  # <source dir> <target root>
  local src="$1" root="$2" name dest
  name="$(basename "$src")"; dest="$root/$name"
  if [ "$uninstall" = 1 ]; then
    if [ -L "$dest" ] && [[ "$(readlink "$dest")" == "$REPO"/* ]]; then run rm "$dest"; echo "removed $dest"; fi
    return
  fi
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "SKIP $dest exists and is not a symlink (move it away first)" >&2; return
  fi
  run ln -sfn "$src" "$dest"; echo "linked $dest -> $src"
}

for tool in "${selected[@]}"; do
  root="${TARGETS[$tool]}"
  run mkdir -p "$root"
  for src in "$REPO"/skills/*/; do link_one "${src%/}" "$root"; done
done

if [ "$upstream_codex" = 1 ]; then
  [ -f "$REPO/upstream/lhermoso/skills/gh-workflow-suite/SKILL.md" ] || git -C "$REPO" submodule update --init
  run mkdir -p "${TARGETS[codex]}"
  link_one "$REPO/upstream/lhermoso/skills/gh-workflow-suite" "${TARGETS[codex]}"
fi

# Old-style commands with the same name would show up twice as /<name>.
if [ "$uninstall" = 0 ] && [[ " ${selected[*]} " == *" claude "* ]]; then
  for src in "$REPO"/skills/*/; do
    n="$(basename "$src")"
    [ -e "$HOME/.claude/commands/$n.md" ] && echo "WARN ~/.claude/commands/$n.md duplicates skill /$n; remove it" >&2
  done
fi
exit 0
