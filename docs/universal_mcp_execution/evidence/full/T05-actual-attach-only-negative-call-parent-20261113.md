# T05: actual installed MCP attach-only negative call

Status: **one real installed-console `tools/call` observed to fail before dispatch; not successful tool/COMSOL execution or T05/U01–U12 acceptance.** This is separate from the refused T41 doctor harness and the T44 replacement launcher/driver; neither was executed. Do not retry the returned request: its public error explicitly says `safe_retry: false`.

## Before running

The installed provisional macOS ARM64 wheel is `comsol_mcp-0.1.16-py3-none-any.whl`, SHA-256 `434fd5022b1ce9c4ef60714d5e6edae213a2eae1f5ed48f9cb2c7ab84ee67659`, built before the current HOLD T10 candidate. The parent inspected the driver, six selected installed route modules and their dispatch chain, adjudicated the Qwen-route source review and its scope deviations, and ran four in-process fake controls; see `T05-attach-only-driver-qwen-parent-review-20261113.md`. The small CASE-check ordering edit followed that review; parent inspected the edit and repeated the fake controls. Driver SHA-256 was `f40a331cc75761678fc14c05d05585ad78b16eae851dfa5d08a201e932c33f72` before and after the actual attempt. Child served identity/effort remain unattested.

The driver creates a previously absent task-owned run, private cwd/HOME/TMPDIR/MCP home and no `control-private` directory, starts only the task's installed console entry, and sends initialize/initialized/`server_info {}` once with `COMSOL_SERVER_MCP_CONTROL_MODE=attach-only`. It checks the wheel and entry hashes and exactly six installed route module bytes against that wheel before and after. Its direct child is closed by stdin EOF/wait; only that direct process object may receive termination after timeout. It does not read the prior T41 control file, scan foreign processes, call T44, or compare the fenced T20 live guard. Fresh directories and source checks are not an OS sandbox or syscall audit.

## Actual execution and readback

At 2026-11-13 07:05:30 UTC, parent invoked the fixed driver once via the task venv `bin/python -I -B`, from the task case directory with a freshly constructed parent environment; the shell first compared the driver SHA to the pinned value. Actual terminal exit **0**, driver status `PASS_ATTACH_ONLY_NOT_SUBMITTED`; the actual direct child `wait()` returned **0**, reader joined, no reader error and no termination was attempted.

Parent read the saved JSON receipt and both exact protocol files, then a separate Python readback asserted the three sent methods and `server_info` arguments, two response IDs, response/text equality, the following structured response, child status and absence of the private control directory. Readback exited **0**; the tool reported this read-only `-c` command auto-approved by a prior session approval.

- MCP `isError: true`; payload `success: false`, error `CONTROL_REQUEST_NOT_SUBMITTED` with `LocalAttachOnlyError` and `safe_retry: false`.
- Data reported `status: NOT_SUBMITTED`, `operation_dispatched: false`, `execution_state_unknown: false`, `dispatch_stage: pre_dispatch`, `control_startup_outcome: NOT_ATTEMPTED`.
- The path `private/mcp-home/control-private` was absent both after the response per the driver and after direct child exit in the independent readback. This is a path observation, not a whole-process filesystem audit.
- The exact captured session had three client messages and two server responses. Initialize reported server name `comsol-mcp-server`, version `1.30.0`; do not conflate that response string with the wheel's `0.1.16` version or treat it as authenticated executable identity.

Raw artifacts under `_state/t42-offline-arm64-install-parent-20261113/console-attach-only-1/`:

| File | SHA-256 |
|---|---|
| `result.json` | `46477e5eae0d6a4ea96870bf47d8bbb9724e6105434f6617e6dab6ac58a667e7` |
| `protocol.stdin` | `0319088678ed0c93937cdac0cba15faa763fbc2eef37400eefb6d82ace5ed0ec` |
| `protocol.stdout` | `d2d4ec882d068511f5338fa15f002736d39e67dd5f4d53c6987b4989c795f184` |

These hashes bind the saved artifacts, not all installed dependencies or actual in-process import origins. Wheel/entry/six-member checks are narrower than a complete runtime attestation. Reported `NOT_ATTEMPTED` is the product's response; without process/syscall tracing this run does not independently prove the absence of every possible subprocess, external read or write. The observed child exit establishes only that direct child's closure. No old model, Worker, SSH or historical UNKNOWN was intentionally accessed; global absence of all effects is not asserted.

No authenticated principal, positive control-daemon call, successful `server_info` result, model operation, COMSOL solve, native Windows/Intel qualification, durability, final release or global independent review is established. In particular, the actual result is a *negative pre-dispatch check*, not evidence the engine processed an operation.
