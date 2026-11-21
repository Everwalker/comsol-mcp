# T05/T42 — actual installed macOS ARM console discovery of provisional wheel

**Scoped result: PASS for one actual stdio initialize/initialized/tools-list exchange, not COMSOL, a tool call or final release.** On 2026-11-13, the parent ran the direct installed `venv/bin/comsol-mcp` executable once from a fresh task-owned private cwd/home, using the previously reviewed driver; the runner exited **0**. Its direct child started and exited **0** after stdin EOF; the stdout reader joined at EOF and no termination was attempted. The launcher stderr was empty. This is not an emulator or a Python import surrogate: the driver's `Popen` target was the actual installed console entry. The 131-byte installed shebang successfully launched for this attempt.

## Bound candidate and scope

- App wheel `comsol_mcp-0.1.16-py3-none-any.whl` SHA `434fd5022b1ce9c4ef60714d5e6edae213a2eae1f5ed48f9cb2c7ab84ee67659`; filesystem-derived provisional dirty-tree candidate, not a committed/Git-only/final release.
- Installed entry SHA `7469d05df516bf33667e85de45ac10e23daa9ac8f376b99e7e975ce09d09e341`; the bundle's entry-point metadata specifies `comsol_mcp.mcp_server:main`.
- Driver SHA `47f4479c6fdf29051d84f4aa8164a558abc9d6a0add6b798ba72a79e9b761a85`; same bytes as separately reviewed and fake-checked. It reports pre/post matching **207** installed app package files to wheel members. The earlier offline install verifier separately checked 207 installed files and 42 locked distributions. These pre/post comparisons and the executed entry do **not** attest the running interpreter's actual imported module origins.
- This single run used the task's new `console-stdio-1` output and private home, an explicit whitelisted environment, `COMSOL_SERVER_MCP_CONTROL_MODE=attach-only`, and no `tools/call`. The driver enforced one direct target and did not deliberately connect to COMSOL or use SSH, a real Worker or a historic model. No OS-level sandbox, syscall/network trace or universal no-effects proof was present.

## Recorded protocol and readback

The saved outbound transcript contains exactly `initialize` with id 1, `notifications/initialized` without an id, and `tools/list` with id 2. Saved JSON-RPC replies have ids **1 and 2**, no protocol error, negotiated protocol `2025-11-25`, `serverInfo.name=comsol-mcp-server`, and `serverInfo.version=1.30.0`. The serverInfo value is the FastMCP server version, **not** the application wheel version `0.1.16`.

The real one-page `tools/list` contained **112 distinct tool names**, each with an `inputSchema` object; required discoverable names included `server_info`, `server_connect` and `registry_call`. This establishes advertising and schema-object presence, not schema semantic correctness, successful execution, principals, authorization or native operation. Sorted tool-name digest `d5d4ff39b2e771cf65e3fdf2991869df7cf75baa6aecc70a045ea0104993e4e7`. Server stderr was 44 bytes, containing the log `Processing request of type ListToolsRequest`; outer launcher stderr was empty.

Independent parent readback `verify_console_actual.py` SHA `71078e0a1ff04aa67c2e41fd09a7e49c212da49b79e06a4d3a98e44c43dc3d4b` exited **0**, parsed both saved wire directions and receipt, checked exact request order/IDs and response IDs, name uniqueness/schema object presence, receipt/launcher equivalence, wheel/entry/driver identities and fresh private `status.json`. It did not relaunch the console. The task-owned status reported app `version=0.1.16`, `server_running=false`, `attached_to_existing_server=false`, and `status=ready`. Fresh private MCP-home entries were `logs`, `outputs`, `status.json`, `workflow_state.json`; no `control-private` entry was observed by this single exact-path check. `ready` is an application status field, not proof of a running COMSOL server.

## Raw artifacts

Under `_state/t42-offline-arm64-install-parent-20261113/`:

- `console-stdio-1/protocol.stdin` SHA `d6dc63c82014d0a373f81e58d9e88464b913ca37668999ac4e3a8ef6c7ae874c`.
- `console-stdio-1/protocol.stdout` SHA `f88309db1de8d7d50651e720b0f2bf7361f65e5db80589fc8b7b069646cdd07e`.
- `console-stdio-1/result.json` SHA `da6d439eb218a1f44109c2d9ee76130bc512a6c412ee9569b778cf7a601cfa0e`.
- `console-stdio-1/server.stderr` SHA `cd20d1aa93e658cb9030369423498ab01104fe5a67117f3b778e1b23cebbc5b8`.
- `console-1.launcher.stdout` SHA `566171d4c51ae2a540077aefa43593f5dd317e860da7322f9c746031f65d0e67`; `console-1.readback.stdout` SHA `5e6f09af972f90011c8f069d8c8c98bb699f49c94fd6036b44c63d4a6333d39e`.

Parent adjudications of the Qwen startup and driver reviews are in `T05-installed-mcp-startup-qwen-parent-adjudication-20261113.md` and `T05-console-driver-qwen-parent-adjudication-20261113.md`; the startup review's mistaken `_g2_engine` import exclusion and blanket no-side-effects claims are explicitly corrected there. These are not final independent project review. The direct real attempt is complete and must not be repeated merely to manufacture more evidence.

## Not established

No `tools/call`, COMSOL version/license/engine/model, real principal, job/Worker, solver, persistence, Windows/Intel package, clean-Git release, source freeze, OS sandbox, or final U01–U12 acceptance is established by this discovery. The provisional wheel still contains the separately observed T10 schema-version issue. `ACTIVE_NOT_COMPLETE` and independent final review `NOT_RUN` remain unchanged.
