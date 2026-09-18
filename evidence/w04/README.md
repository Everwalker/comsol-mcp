# W04 evidence status

Software regression tests passed locally on 2026-09-18 with the historical
project virtual environment: `45 passed`. This is control-flow evidence only.
No COMSOL Server was started, connected, or modified because W02 held the
serialized runtime slot. Real MCP/backend cases T003, T005, T006, T007, and
T033 are **NOT_RUN** until that slot is available. The integration run must
preserve requests, responses, assertions, engine log, before/after snapshots,
the resulting MPH, and a fresh-process reopen result.
