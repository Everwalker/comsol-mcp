# Explicit Windows SCM attachment

`COMSOL_SERVER_MCP_SCM_ENDPOINT_CONFIG` selects attach-only mode in the normal
installed `comsol-mcp` transport. Its value is an absolute canonical Windows
path to an operator-managed, current-client-user private locator file. A blank
value also selects this mode and refuses; it does not select the default home.

The locator has exactly three fields:

| Field | Required value |
| --- | --- |
| `schema` | `comsol-mcp.scm-client-endpoint/1` |
| `service_name` | The exact existing `ComsolMCP_` service name |
| `control_home` | The exact absolute service state directory supplied to the daemon's `--home` |

Use UTF-8 JSON with no duplicate or extra fields. The locator contains no
token, endpoint address, PID, SDK provider, receipt, or self-asserted proof.
The transport never appends `control-private` to this explicitly selected home.
The ordinary default home and startup behavior are unchanged when this
environment variable is absent. Non-Windows attachment refuses.

The dedicated reader uses only a closed set of readonly Windows SDK exports.
It reuses the established private-path ACL policy for the locator and ancestor
traversal. Its separate target policy requires SYSTEM ownership and a protected
DACL on both the state directory and `control.json`. Effective writers must be
SYSTEM or Administrators; other data readers may only be the actual current
client SID and the exact SCM-observed service SID, with read/execute rights.
No global private-owner rule is relaxed. A locator does not create these ACLs
or grant the client access to the service state.

Original no-reparse path handles, full volume/file identity, extent, creation,
write/change metadata, raw security, and file bytes are compared while pinned.
LastAccessTime is excluded. Readonly SCM queries must identify the exact
RUNNING, own-process, LocalSystem service with a restricted service SID. Its
configured binary argv must run normal Python with `-B -I -m
comsol_mcp._control_daemon`, the exact home and service name. An independently
opened query-only process HANDLE must agree with the protected endpoint's
PID/birth, actual SYSTEM token user and binary image. SCM configuration is
compared again; this is not a claim to read the process's complete command line.

The bearer is read privately from the protected endpoint file. It is never a
locator field, a documented example, an error detail, or an evidence receipt.
Only the existing fixed `127.0.0.1` HTTP transport receives it. After native
source checks and confirmed original cleanup, one bounded `session_health`
preflight must succeed before the requested operation is sent.

Source/health refusal returns `CONTROL_REQUEST_NOT_SUBMITTED` with
`control_startup_outcome=NOT_ATTEMPTED`. No directory creation, chmod, log,
token copy, alternate home/provider, daemon spawn, service start/stop or PID
signal is attempted. A lost response after the requested operation was sent
keeps the existing UNKNOWN/idempotency behavior and does not resubmit it.

Original output storage, SDK bindings, raw returns and closers remain strongly
rooted on an uncertain acquisition/release. Such a reader refuses further
queries and close retries. Private original-owner recovery can acknowledge
cached successful cleanup bookkeeping; it cannot replace a closer or replay
an opaque native call. Readable memory alone never grants allocation ownership.

This source/software stage has no native-access approval. In particular, the
current daemon endpoint publisher uses ordinary file creation and chmod; this
unit adds no publisher that guarantees a protected SYSTEM DACL. The required
target ACLs and normal cross-user delivery remain prerequisites to be reviewed
by Main under a separate authorized deployment step. Nothing here is host
apply-ready, a service installation, native isolation proof, or T037 admission.
