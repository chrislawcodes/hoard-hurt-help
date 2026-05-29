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
#
# The worktree is created as a sibling of the repo:
#   <repo>/../<repo-name>--<branch-with-slashes-as-dashes>
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

case "${1:-}" in
    new)  shift; cmd_new "$@" ;;
    list) shift; cmd_list "$@" ;;
    rm)   shift; cmd_rm "$@" ;;
    *)
        echo "usage: scripts/agent-worktree.sh {new|list|rm} [branch-name]" >&2
        exit 2
        ;;
esac
