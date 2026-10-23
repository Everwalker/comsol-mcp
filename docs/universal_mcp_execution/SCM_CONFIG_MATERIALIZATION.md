# Original Windows Bootstrap V2 static configuration

This source unit extends the original local installer. It does not authorize
installation, start SCM, grant T037 admission, transfer capabilities to another
process, or qualify Windows enforcement. Actual host execution is NOT_RUN.
The initial SYSTEM provisioning/runtime context remains NOT_QUALIFIED.

The frozen source base is `ed09e598c34fc96fb636fd1bc72214b641b289f3`.
The parent materialization contract, actor clarification, and restricted-service
state clarification, private-source compatibility and exact new-root decisions are separate
preserved private inputs. The actor
clarification permits the same existing elevated Admin/already-capable SeRestore
static writer; it does not permit Admin to impersonate a SYSTEM loader. No
privilege is enabled, token replaced, account created, or new helper introduced.

## Original intent and four files

V1 input/argv/inventory behavior is preserved. V2 uses
`comsol-mcp.windows-initial-host-bootstrap/2`, adds the required `provisioning`
field, and binds the exact original raw bytes/SHA. The exact `BootstrapIntent`
is reparsed by the original owner and by the unchanged installer-context gate.
Copying/replacing an owner, changing typed fields, or submitting a matching-looking
manifest does not confer authority.

The original Python/JDK/archive file inventory remains required and pinned.
Generated configuration files are four separately sanctioned nodes, not source
files copied from an unapproved tree:

| Relative node | Existing production schema |
| --- | --- |
| `profiles/trusted-profile.json` | `comsol-mcp.windows-t037-profile/4` |
| `profiles/host-provider.json` | `comsol-mcp.host-scoped-provider-config/1` |
| `profiles/memory-resource.json` | `comsol-mcp.memory-resource-profile/v1` |
| `profiles/disk-resource.json` | `comsol-mcp.disk-resource-config/v1` |

There are no default budgets, estimates, operation grants, ports, credentials,
authorization references or expiry values. Missing approved fields block parsing.
The provider accepts only actions published in the frozen source; no parallel
managed-stop/provider WIP is consumed. Empty provider arrays, when explicitly
approved, create no grants. Memory/disk remain admission-only, not hard quotas.

`provisioning` has the closed recipe schema
`comsol-mcp.windows-scm-config-recipe/3` and these required fields. Recipes `/1`
and `/2` are rejected: an old raw approval is never silently reinterpreted under
the corrected private-source or root-holder security policy.

| Field | Required approved input |
| --- | --- |
| `host` | Exact `hostname` and `machine_fingerprint_sha256`, separately named domains |
| `project_root` | `{kind: NEW_DIRECTORY, relative: <approved new directory>}` or `{kind: EXISTING_DIRECTORY, identity: <full PathIdentity>}` |
| `owned_directories` | Explicit bounded array, including every new parent; existing roots may use an explicit empty array |
| `labels` | Closed `file`/`directory` policies, each exact `rid`/`mask`/`flags` |
| `state_policy` | Exact service-SID slot, explicit bounded `service_access_mask`, explicit `client` null or approved client row |
| `templates` | The four exact relative filenames above and complete existing-schema documents |

Existing roots and finite external installation/image targets are readonly
observations. Their original full IDs, raw SDs, explicit labels and content are
compared; they are never adopted as newly created nodes, relabelled, or deleted.
New root IDs are read from the original newly created HANDLE. Numeric IDs are
not invented by the compiler.

Only a literal object `{"$bootstrap":"<slot>"}` is accepted, at a sanctioned
position. Required bindings are `service_sid` in the isolation policy,
`project_root` in the host/disk roots, and `storage_id` in the disk profile.
`directory_descriptor_hex`/`file_descriptor_hex` are permitted only for owned
finite target descriptors outside the private source directory. Its exact
`profiles`/`config_rx` row requires `private_directory_descriptor_hex`; it cannot
be a mutable or code target. No callback, expression language, source trust flag,
incoming resolved facts or computed grant/source-hash slot exists.

The exact approved `NEW_DIRECTORY.project_root.relative` requires the distinct
`root_holder_descriptor_hex` slot on its directory/`project_root` row. It is a
readonly root holder, with the same protected SYSTEM/SY/BA policy as `profiles`.
Its emitted descriptor comes from that original newly owned node's SD readback,
with its full volume/file identity and explicit LABEL. Combined mutable/code
roles or explicit WRITE grants on that holder fail before any creation; approved
mutable data/temp must use distinct leaves. This rule applies to the exact raw
recipe node, not a directory name, every ancestor, or existing/external roots.

Before effects, the compiler checks the existing closed schemas and independent
relations. For this discarded schema projection only, the approved AppContainer
SID occupies the unresolved service-SID position and new IDs remain explicitly
symbolic strings. It is neither an observed service SID nor emitted configuration.
After the original stopped service supplies its actual SDK lookup SID, every
production profile/finite/network/memory validator and shared disk data validator
is rerun with the original root/security facts. Provider structural intent checks
use published constants; filesystem sink/owner/expiry checks still belong to its
unchanged normal loader. No weaker parser supplies loader attestation.

All four final UTF-8 byte sequences and hashes are frozen together before the
first generated-file write. `config_rx` is the owned directory with null content
hash. The source file hash is obtained by the normal loader, outside its JSON;
there is no recursive self-hash or grant minting.

## Static security and original ownership

Creation is exclusive, initially SYSTEM-owned/protected SY/BA DACL. Each newly
owned directory/file receives an explicit approved Medium-or-higher label with
NO_WRITE_UP. Flags are zero: no inherited-only/default label is substituted and
no propagation over existing host trees is requested. DACL and LABEL snapshots
are distinct; the existing DACL parser still refuses SACL-bearing descriptors.

The four generated startup files and their final `profiles` source directory
remain SYSTEM-owned/protected SY/BA-only, with no service/user/World/AppContainer
ALLOW. The protected directory and files do not inherit the parent's service RX.
This follows Main's bounded decisions; the original private loader guards and
trusted SID set are unchanged. The exact new root holder also remains SY/BA-only.
Other code, parent and leaf target classifications retain their existing policy.
The original SCM-observed service SID supplies
a separate, raw-intent-approved `service-state` template. SY/BA retain full control.
The state directory's service data mask must be a nonzero subset of `0x1301BF`,
OI|CI, with no WRITE_DAC/WRITE_OWNER/system-security/generic-all/unrecognized bits.
This describes approved static rights; smaller masks do not prove SQLite access.
AppContainer/World/foreign writers receive no state permission. Finite A project
data/temp policy is not widened by this state template.

The optional client is exactly `{sid, directory_access_mask}` with non-inheritable
`0x1200A0`. This bounded producer supports only an explicitly approved User SID
equal to the original observed current installer TokenUser, rechecked at effect.
It never substitutes the caller automatically. A different user, or SYSTEM with
an external client needing a separate actual-User producer, is INCOMPLETE_INPUT.
Explicit `client: null` supports both SYSTEM and capable Admin static writers.
No client read ACE is inherited onto DB/WAL/SHM/lock/log/config files.

Endpoint/stage files are not created here. Their later publisher must create
explicit protected SD/LABEL, service data Modify and (if approved) client
`0x120089` flags zero. It must not demand WRITE_DAC/OWNER at runtime. This unit
adds neither a fifth file nor a publisher/consumer/private-owner exception.

LABEL mutation uses the original HANDLE, fixed SDK function, `SE_FILE_OBJECT`,
`LABEL_SECURITY_INFORMATION` and only the validated SACL pointer. DACL mutation
uses the existing bounded O/G/protected-DACL operation on the new owned nodes.
DWORD zero is the only confirmed success. Complete original buffers/arguments,
before images, return slots and owner calls are retained before SDK dispatch.
The narrow LABEL operation does not imply full-SACL privilege or capability.
See Microsoft's [SetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo)
and [SECURITY_INFORMATION rights](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information).

Opaque/partial effects and uncertain acquire/write/flush/set/close outcomes retain
the original owner and fence further queries/mutations. An exact cached-success
LABEL/DACL postimage whose Python acknowledgement failed can be acknowledged
through `Bootstrap.acknowledge_configuration()` without any SDK call or setter
replay. It permits original compare-and-restore, not another apply attempt.

The native companion checks both cached setter/getter object identities against
the Backend's original frozen function bindings and snapshots of `restype` and
`argtypes` contents. Equal-looking wrappers or newly rebound method objects do
not replace an original cached function. Getter and setter dispatch retain the
validated original local function, and recheck binding before dispatch after
intervening reads. The getter's actual call record uses that same original
function. The general Bootstrap `_Call` and Backend implementation are unchanged.

## Flags, handoff and normal loaders

V2 argv has one fixed order: normal prefix Python, `-B -I -m
comsol_mcp._control_daemon`, then `--home`, `--windows-scm-service-name`,
`--windows-t037-profile`, `--host-provider-config`, `--memory-resource-config`,
`--disk-resource-config`, each with the exact approved value. Windows quoting and
SCM readback must match. V1 argv is unchanged. There is no shell/env/next-free
substitution and no StartService/COMSOL action.

The existing original writer -> bridge -> runtime pin handoff compares the whole
namespace, original byte/SD/LABEL/identity snapshots and stopped SCM object.
Unknown close does not drop a pin. Restore compares first, deletes only original
new nodes, preserves external/source nodes, and refuses extra children or drift.

`Bootstrap.load_configurations()` calls the four unchanged normal loaders, only
under the actual SYSTEM token, retaining original returned objects and their
source identities. Admin is refused before loader dispatch. Any uncertain partial
loader result retains original state; serialized diagnostics are not capabilities.

The current software positive executes all four unchanged normal loaders, their
private-path/owner/DACL guards, real SD/LABEL parsers and source-origin producers
over explicitly synthetic SDK exports. The provider retains a real disposable
Python fd, with path and borrowed-HANDLE translation only at the OS seam. No raw
reader, predicate, source-chain hash, loader object or typed attestation is
substituted. Requested file rights are recorded and checked as read-only. This
is SOFTWARE_ONLY, not an actual SYSTEM token/Windows/SCM load.

Both the explicitly approved `EXISTING_DIRECTORY` root and corrected exact
`NEW_DIRECTORY` holder execute all four normal loaders in the current software
run. The old root service-RX refusal remains reconstructable in R23 and is
injected again as a normal disk-loader negative. Wrong root owner and incompatible
root/mutable/code roles also refuse. No disk/provider guard changed and no
existing/external root was rewritten to make the positive pass.

Service RX on a final private source remains a real normal-loader negative.
Final private-directory guards and the original Bootstrap snapshot compare are
also tested. T037/memory/disk use the separately documented pinned-ancestor
traversal rule; safe readonly ancestor access is not falsely reported as a final
private-file refusal. The positive has explicit empty provider destinations and
grants: it proves no configured mutable-sink access, grant authority or expiry.

R14's 680 PASS/one deselection, its old service-RX counter and exact source packet
and R23's 720 PASS/one deselection/source/root-refusal packet are preserved
separately. They are not relabelled as this corrected candidate.

R27's 730 software packet is also frozen separately. Subsequent valid software
reproduction confirmed that its cached setter alias could be replaced without
the old check refusing. Main's copied test function is byte/AST identical under
the proper autouse synthetic host fixture: R28 is the real failing counter,
followed by the repaired candidate's positive result. Main's earlier dependency
and missing-fixture preparation errors remain distinct and are not binding
counter FAIL/PASS. No runtime source or native evidence was changed to repair it.

The installer pins are same-process continuity, not a capability handoff to the
SCM daemon. SYSTEM daemon load/ack, publisher/locator delivery, restricted-service
access enforcement, A/B/C integration, T037/Resume and startup remain unqualified.

## Reproducible software verification

The compact evidence points to a clean frozen-base mirror plus the six leased
source/test overlays, source snapshots, full input/import hashes, JUnit and raw
failure records. Test fixtures are synthetic and do not depend on private host
plans, license material or tokens. Filesystem tests use `/private/tmp`.
The existing noowners-volume negative uses a newly created empty mirror-local
scratch directory, with no historical private contents copied. An existing
Darwin memory-native observation test was attempted/unavailable in earlier
expanded runs; those original skips are preserved. It is explicitly deselected
from the final software run and is not counted as software/native success.

The final single coherent run records actual candidate execution, not just
post-pytest module paths. Process-global `SourceFileLoader` hooks compile captured
current source bytes, with actual `exec` audit events matched to the exact code
objects. Pytest's original rewrite algorithm reads captured bytes; stale owned
rewritten pyc is bypassed. Source/code blobs and per-module source before/after
hashes are retained. Cross-thread execution and a deliberately mismatched loader
byte stream are recorder controls only. They establish no native capability.

Software controls exercise original Bootstrap prepare/apply/handoff/restore,
actual strict schema/ctypes parsers, original node ownership, exclusive writes,
LABEL/DACL effects, lost-result/ack fences, finite state/client rights, actual-ID
slots, fixed flags, both normal four-loader root-mode positives, read-only
requested rights and unchanged private-loader refusals. Windows SDK exports
are software boundaries. No result establishes native Windows, service-write,
MIC enforcement, cross-user delivery, host apply or COMSOL acceptance.

Main accepted this bounded source/software unit after final R30: 753 PASS, zero failures/errors/skips, one explicit Darwin-native exclusion. The R28 one-failure counter has frozen-source/postrun provenance, without an exec-event trace; it is not assigned R30 execution-binding depth. R30 captures121 actual module execs (104 source loaders/17 pytest rewrites) and244 source/code blobs. Full922 input after-map was not saved: equality is a runner assertion/summary, with121 executed byte/code pairs and7 physical snapshot files mechanically checked. Per-node deselection event roster is also unavailable; the exact command filter and raw count remain. Current e1bba combined runtime/native behavior was not exercised by this ed09-derived unit.
