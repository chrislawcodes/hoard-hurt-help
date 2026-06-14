#!/usr/bin/env bash
#
# Give each parallel agent (Claude, Codex, Gemini, …) its own isolated git
# worktree so concurrent sessions never edit the same files in the same folder.
#
# Why: when two agents share one working directory, their edits clobber each
# other — a file can flip between two half-finished states between commands, and
# one session's work can get swept into another's commit. A worktree gives each
# agent a separate checkout backed by the same .git, so they stay out of each
# other's way while still sharing branches and history.
#
# Usage:
#   scripts/agent-worktree.sh new <branch-name>     Create a fresh worktree off origin/main
#   scripts/agent-worktree.sh list                  Show all worktrees
#   scripts/agent-worktree.sh rm <branch-name>      Remove a worktree and delete its branch
#   scripts/agent-worktree.sh prune [--yes]         Sweep every worktree whose branch is
#                                                   already done. Dry-run unless --yes.
#
# The worktree is created as a sibling of the repo:
#   <repo>/../<repo-name>--<branch-with-slashes-as-dashes>
#
# Note: archiving or closing a chat does NOT clean up a worktree — folders live on
# disk on their own. `prune` is the safety net: run it any time to sweep finished
# worktrees. It never touches the main checkout, anything with uncommitted edits,
# or a branch that still has commits not yet on origin.
#
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
repo_name="$(basename "$repo_root")"

_worktree_path() {
    # Turn a branch name into a filesystem-safe sibling directory.
    local branch="$1"
    printf '%s/../%s--%s' "$repo_root" "$repo_name" "${branch//\//-}"
}

cmd_new() {
    local branch="${1:?usage: agent-worktree.sh new <branch-name>}"
    local path
    path="$(_worktree_path "$branch")"

    # Always branch off the freshest main so the agent starts in sync.
    git -C "$repo_root" fetch origin main
    git -C "$repo_root" worktree add "$path" -b "$branch" origin/main

    echo
    echo "Worktree ready for branch '$branch':"
    echo "  cd \"$(cd "$path" && pwd)\""
    echo "Work there, commit, push, open the PR. Tear it down with:"
    echo "  scripts/agent-worktree.sh rm $branch"
}

cmd_list() {
    git -C "$repo_root" worktree list
}

cmd_rm() {
    local branch="${1:?usage: agent-worktree.sh rm <branch-name>}"
    local path
    path="$(_worktree_path "$branch")"

    # Run removal from the main repo, never from inside the worktree being removed.
    git -C "$repo_root" worktree remove "$path" --force
    # Squash-merges rewrite history, so the local branch won't read as merged: -D.
    git -C "$repo_root" branch -D "$branch" 2>/dev/null || true
    git -C "$repo_root" worktree prune
    echo "Removed worktree and branch '$branch'."
}

# A branch is "done" (safe to delete) when its work is already preserved off your
# laptop: it has no unique commits, OR its tip is already on origin/main, OR it has
# a merged PR on GitHub. Anything else is treated as active and kept.
_branch_is_done() {
    local branch="$1" tip ahead
    tip="$(git -C "$repo_root" rev-parse --verify --quiet "$branch")" || return 1
    ahead="$(git -C "$repo_root" rev-list --count "origin/main..$branch" 2>/dev/null || echo 1)"
    [ "$ahead" = "0" ] && return 0
    git -C "$repo_root" merge-base --is-ancestor "$tip" origin/main 2>/dev/null && return 0
    # gh is optional; if present, a merged PR also counts as done.
    if command -v gh >/dev/null 2>&1; then
        local merged
        merged="$(gh pr list --head "$branch" --state merged --json number --jq '.[0].number' 2>/dev/null || true)"
        [ -n "$merged" ] && return 0
    fi
    return 1
}

cmd_prune() {
    local apply=0
    [ "${1:-}" = "--yes" ] && apply=1

    git -C "$repo_root" fetch origin main --quiet || true

    local path="" branch="" removed=0 kept=0 skipped=0
    while IFS= read -r line; do
        case "$line" in
            worktree\ *) path="${line#worktree }" ;;
            branch\ *)   branch="${line#branch refs/heads/}" ;;
            "")
                _prune_one "$path" "$branch" "$apply"
                path="" branch="" ;;
        esac
    done < <(git -C "$repo_root" worktree list --porcelain)
    [ -n "$path" ] && _prune_one "$path" "$branch" "$apply"

    echo
    if [ "$apply" = "1" ]; then
        echo "Pruned $removed worktree(s). Kept $kept active, skipped $skipped dirty."
    else
        echo "Dry run: $removed would be removed, $kept active, $skipped dirty/detached."
        if [ "$removed" -gt 0 ]; then
            echo "Re-run with --yes to remove them: scripts/agent-worktree.sh prune --yes"
        fi
    fi
    return 0
}

# Decide and act on a single worktree. Reads/updates the counters in cmd_prune.
_prune_one() {
    local wt_path="$1" wt_branch="$2" apply="$3"
    [ -z "$wt_path" ] && return 0
    # Never touch the main checkout itself.
    [ "$wt_path" -ef "$repo_root" ] && return 0
    # Detached HEAD has no branch to reason about — leave it alone.
    if [ -z "$wt_branch" ]; then
        echo "skip (detached HEAD):     $wt_path"; skipped=$((skipped + 1)); return 0
    fi
    # Uncommitted edits could be unsaved work — never auto-delete those.
    if [ -n "$(git -C "$wt_path" status --porcelain 2>/dev/null)" ]; then
        echo "skip (uncommitted edits): $wt_branch"; skipped=$((skipped + 1)); return 0
    fi
    if _branch_is_done "$wt_branch"; then
        if [ "$apply" = "1" ]; then
            git -C "$repo_root" worktree remove "$wt_path" --force
            git -C "$repo_root" branch -D "$wt_branch" 2>/dev/null || true
            echo "removed (done):           $wt_branch"
        else
            echo "would remove (done):      $wt_branch"
        fi
        removed=$((removed + 1))
    else
        echo "keep (active, unmerged):  $wt_branch"; kept=$((kept + 1))
    fi
}

case "${1:-}" in
    new)   shift; cmd_new "$@" ;;
    list)  shift; cmd_list "$@" ;;
    rm)    shift; cmd_rm "$@" ;;
    prune) shift; cmd_prune "$@" ;;
    *)
        echo "usage: scripts/agent-worktree.sh {new|list|rm|prune} [args]" >&2
        exit 2
        ;;
esac
