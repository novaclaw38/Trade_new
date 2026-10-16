#!/usr/bin/env bash
# One unattended trading cycle for a fresh, short-lived machine (e.g. a scheduled
# cloud session). The bot's memory lives on a separate git branch so it survives
# between runs:
#
#   1. check out the data branch (default: bot-data) into a side folder
#   2. run `python -m bot run` with DATA_DIR pointing at it
#   3. commit and push whatever changed (state.json, logs/)
#
# To halt NEW trades from anywhere, add a file named STOP to the data branch.
# API keys come from the environment, as usual.
set -euo pipefail

DATA_BRANCH="${DATA_BRANCH:-bot-data}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORKTREE="${WORKTREE:-$REPO/.bot-data}"

cd "$REPO"
python -m pip install -q -r requirements.txt

push() {
  for delay in 2 4 8 16 0; do
    git -C "$WORKTREE" push -u origin "$DATA_BRANCH" && return 0
    [ "$delay" -gt 0 ] && sleep "$delay"
  done
  echo "Push of $DATA_BRANCH failed" >&2
  return 1
}

fail() { echo "$1 Not trading." >&2; exit 2; }

# Only a remote that answers "no such branch" (exit 2) means start a new one.
# Any other failure (network, access) must not start from empty memory.
remote_status=0
git ls-remote --exit-code --heads origin "$DATA_BRANCH" >/dev/null || remote_status=$?
if [ "$remote_status" -eq 0 ]; then
  git fetch -q origin "$DATA_BRANCH" || fail "Could not fetch $DATA_BRANCH."
  if [ -d "$WORKTREE" ]; then
    # A reused folder: bring in the latest state and any STOP file first.
    git -C "$WORKTREE" merge -q --ff-only "origin/$DATA_BRANCH" \
      || fail "$WORKTREE has diverged from origin/$DATA_BRANCH; reconcile it by hand."
  else
    git worktree add -q -B "$DATA_BRANCH" "$WORKTREE" "origin/$DATA_BRANCH"
  fi
elif [ "$remote_status" -eq 2 ] && [ ! -d "$WORKTREE" ]; then
  echo "Creating new data branch $DATA_BRANCH"
  git worktree add -q --orphan -b "$DATA_BRANCH" "$WORKTREE"
  git -C "$WORKTREE" commit -q --allow-empty -m "Start $DATA_BRANCH"
else
  fail "Could not check whether $DATA_BRANCH exists on the remote."
fi

# If the result can't be saved, the next run would start without its recorded
# stops and close any open position. Uncertainty means no trade: check push
# access before trading.
git -C "$WORKTREE" push -q --dry-run origin "HEAD:refs/heads/$DATA_BRANCH" \
  || fail "Cannot push to $DATA_BRANCH, so the run's state could not be saved."

status=0
DATA_DIR="$WORKTREE/data" KILL_SWITCH_FILE="$WORKTREE/STOP" python -m bot -v run || status=$?

git -C "$WORKTREE" add -A
if git -C "$WORKTREE" diff --cached --quiet; then
  echo "No data changes to save."
else
  git -C "$WORKTREE" commit -q -m "Bot run $(date -u +%Y-%m-%dT%H:%MZ) (exit $status)"
  push
fi
exit "$status"
