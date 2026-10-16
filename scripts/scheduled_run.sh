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

if [ ! -d "$WORKTREE" ]; then
  if git fetch -q origin "$DATA_BRANCH" 2>/dev/null; then
    git worktree add -q -B "$DATA_BRANCH" "$WORKTREE" "origin/$DATA_BRANCH"
  else
    echo "Creating new data branch $DATA_BRANCH"
    git worktree add -q --orphan -b "$DATA_BRANCH" "$WORKTREE"
  fi
fi

# If the result can't be saved, the next run would start without its recorded
# stops and close any open position. Uncertainty means no trade: check push
# access before trading.
if git -C "$WORKTREE" rev-parse -q --verify HEAD >/dev/null \
   && ! git -C "$WORKTREE" push -q --dry-run origin "HEAD:refs/heads/$DATA_BRANCH"; then
  echo "Cannot push to $DATA_BRANCH, so the run's state could not be saved. Not trading." >&2
  exit 2
fi

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
