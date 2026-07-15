#!/bin/bash
# Stop hook: forces Copilot to run the `code-reviewer` skill before it's allowed
# to end a turn that touched trajectory_to_tests/ or tests/.
#
# Docs: https://code.visualstudio.com/docs/agent-customization/hooks#_stop
#
# Safety: `stop_hook_active` is true when this hook has ALREADY blocked once
# this turn and the agent came back around to stop again. We only force one
# extra turn, then let it stop — without this check the agent could loop
# forever (and each forced turn burns AI credits).

INPUT=$(cat)

STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || echo "false")
if [ "$STOP_ACTIVE" = "true" ]; then
  echo '{"continue": true}'
  exit 0
fi

# Not a git repo (or git not installed) -> fail open, don't block.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo '{"continue": true}'
  exit 0
fi

# Anything changed (modified, staged, or new/untracked) under the source or
# test trees this session? git status catches new files that `git diff` misses.
CHANGED=$(git status --porcelain -uall -- trajectory_to_tests tests 2>/dev/null || true)

if [ -z "$CHANGED" ]; then
  echo '{"continue": true}'
  exit 0
fi

FILES=$(echo "$CHANGED" | awk '{print $2}' | tr '\n' ' ')

jq -n --arg files "$FILES" '{
  hookSpecificOutput: {
    hookEventName: "Stop",
    decision: "block",
    reason: ("Uncommitted changes this turn in: " + $files + ". Before finishing, invoke the code-reviewer skill against these changes (both the Standards and Spec axes), fix any Critical findings in-place, then report the review summary to the user.")
  }
}'
