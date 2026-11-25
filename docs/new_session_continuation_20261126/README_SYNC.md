# Continuation progress (public subset)

This directory synchronizes the task plan, target contract, navigation tool,
and the single writable session ledger `state/CURRENT.json`. Use
`python3 tools/next_tasks.py --state state/CURRENT.json` from this directory.
The ledger inherits the scoped statuses in `references/CURRENT_20261123.json`;
it does not certify the current product or restore native execution authority.

The original continuation package remains local. This public subset does not
include its historical supplemental G3 evidence, original archives, raw
captures, runtime resources, or the generated full source map. It is not a
replacement for the complete package or a claim that those bytes were reviewed.
