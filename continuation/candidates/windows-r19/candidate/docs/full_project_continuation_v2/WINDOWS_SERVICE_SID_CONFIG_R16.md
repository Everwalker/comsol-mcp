# Windows existing-service SID configuration leaf (R16)

This is a SOURCE_ONLY successor to accepted R15. It implements the original
T037 host-setup requirement for one already active, exact OWN_PROCESS service.
The account and service must already exist. No service installation, credentials,
start/stop, restart, ACL, AppContainer, WFP or account mutation is provided here.

The existing trusted profile still selects RESTRICTED. A closed desired object
`comsol-mcp.windows-service-sid-config/1` adds only an explicit schema to the five
original Windows service identity fields. Only one `windows_service UPDATE` is
admitted. The old five-field intent and all other legacy grammars retain their
original meanings; no old plan is upgraded implicitly. The original per-resource
journal accepts a one-step mutation-sequence/4 branch. Receipt body/4 binds the
same original action ownership GUID as a resource-instance identifier (it is not
an SCM native GUID), original baseline hash, backup path and configured hash.

The private reader on CtypesWindowsPolicyApi reads bounded complete SCM config,
SID config, status, security, account and service SIDs, argv and the pinned
launcher image. It preserves the public observe_service payload and its native
RESTRICTED predicate. Both static and status service types must equal
SERVICE_WIN32_OWN_PROCESS, the active PID must equal the current sealed service
process, and account/SIDs and the unique launcher path/image/argv hashes must
match the trusted profile. Baseline SID types 0 or 1 are accepted only for this
setup leaf. Already-3 returns a pre-effect refusal without an applied receipt.
Unsupported bits, shared/foreign services and changed selectors are refused.

A pre-existing private backup is actually read through the original _API,
NativeOwnedRegistry, preflight owner and full double security-path verifier.
Only the final file gains READ_DATA alongside READ_CONTROL; old metadata callers
and private ACL predicates remain unchanged. Both complete security chains pass
before a private retained callback runs. Identity/size/timestamps/link count,
EOF, actual raw SHA, canonical JSON and complete path/ACL stability are checked
while the original handles remain held. The closed credential-free backup object
contains schema `comsol-mcp.windows-service-sid-backup/1`, profile SHA, project ID,
host ID, root and the complete actual baseline subject. Proposed path/hash or
caller booleans cannot replace actual bytes and the native subject read.
The original 8 MiB plan budget applies to backup bytes; launcher streaming uses
the existing original 2 GiB image bound. A Windows read callback never creates
an additional file descriptor or backup writer.

The original scoped provider/authority/nonce/Controller admission is reused.
First durable INTENT precedes ChangeServiceConfig2W level 5. The manager requests
SC_MANAGER_CONNECT only. The chosen service requests QUERY_CONFIG, QUERY_STATUS,
READ_CONTROL and CHANGE_CONFIG only for configuration. Same-handle readback and
a genuinely fresh read-only SCM handle must show the full original subject with
only the selected SID field changed. No WFP transaction is claimed. Restore
requires the original successful durable apply receipt/body/4, current exact
configured subject, the same pinned backup and separately authorized fresh
reverse fingerprint; only the original 0/1 value is written.

Native SDK handles, LocalFree allocations, query buffers and file buffers use
the original registry and captured first SDK closers. BOOL accepts any nonzero
signed-32 integer, rejects synthetic Python booleans and malformed domains.
DWORD, pointer-width handles, returned buffer extents and in-buffer strings are
checked. CommandLineToArgvW's contiguous LocalFree allocation is bounded with
captured LocalSize before its pointer array/string offsets are decoded. File
ReadFile count cannot exceed the requested bytes. The real Controller retains
its original request/executor plus actual auxiliary file preflight owners;
UNKNOWN, I/O and cleanup failures retain first causes and strong roots through
GC. Explicit original-entry cleanup does not read paths, repeat authorization,
replay configuration, roll back the SID or alter historical Store UNKNOWN.
The Controller reconciles only after the original registries and auxiliary
preflight owners confirm terminal cleanup. No generic registry or other family
contract is changed. If a direct HANDLE-returning acquisition throws before any
return value is available, the original actual SDK context remains explicitly
unclassified; empty registry is insufficient for reconciliation or another SDK
session. No invented handle or metadata can resolve that uncertainty.

Source/software results are CONFIGURED_UNVERIFIED_SYSTEM_RESTART_REQUIRED or
RESTORED_UNVERIFIED. Microsoft states that the SID configuration change takes
effect on the next system start. No current token proof, AppContainer/WFP policy
issuer, _pending permission or Factory resume capability is produced. Windows
kernel testing remains NOT_RUN. First account/service bootstrap, the staged
AppContainer root-ACL conflict, loopback/vendor compatibility, storage-backed
hard quotas, whole filesystem/network isolation and child proof issuance remain
original OPEN requirements. Full T037/W26/Goal acceptance is not claimed.

Software controls exercise the actual original sealed SCM issuer, Controller,
Executor, OperationStore, Provider, API/registry and SDK boundaries using public
synthetic data only. Old failures remain visible. Ten macOS memory integration
failures observed in the R16 affected run remain FAIL; their original R13/R14
baseline and actual older errno-1 diagnostic are bound separately. They are
excluded explicitly from the final narrow source regression and are not turned
into PASS or an inferred new cause. Native Windows publication tests skipped on
this host remain NOT_RUN, not native PASS.
