# W26 / T037 host isolation and resource implementation plan

Status: implementation plan, not an installed profile or T037 PASS. This closes an original requirement in `00_FULL_SPEC.md` T037 and resource semantics at line1644. It does not change the original26/272/60 acceptance or add a waiver.

## Production boundary

Reuse the existing ControlDaemon, serial Worker lane, owned-server launcher, live process/socket proof, ProjectAuthority, durable OperationStore, host configuration and startup refusal. Do not introduce another execution service. An operator-selected profile must cover **both** the task-owned COMSOL Server and Java Worker plus their descendants. An isolated client talking to a shared high-privilege server is insufficient because model-side external execution occurs on the server.

A caller's execution JSON or a self-written receipt cannot grant this boundary. Trusted host configuration identifies the profile and verifier. Verification binds the exact profile hash, current host/platform, task/project root identity, service account UID/SID distinct from the interactive account, server and Worker PID/birth/image, child inheritance, applied policy identity and resource backing. Unknown or missing proof leaves `os_sandbox=false`; execution requiring this boundary is refused before dispatch, with a typed reason and no revision/job success. Existing trusted-code opt-in is permission, not proof of OS isolation.

Implement the pure schema, validation and trusted provider interface first; integrate only after current shared-file ownership returns. Real host adapters inspect current OS state rather than trusting fixture booleans. Keep capability states IMPLEMENTED_UNVERIFIED / NOT_CONFIGURED / UNSUPPORTED / VERIFIED distinct.

## Reviewable profile contents

Prepare a machine-readable plan and matching apply/verify/restore tooling before asking for account/ACL/firewall/quota changes. Include exact new account/profile/rule names, absence checks, operator-controlled input values and hashes, paths and their baseline identities, rights added, prior ACL/policy backup, rollback identity checks, launch argv/images, and a change manifest. Default is plan-only; missing explicit approval/ref, unsupported host or a name collision refuses apply. No administrator password or license content enters the plan. Restore removes only proven task-owned additions and restores exact approved prior values; a changed foreign policy is preserved with a recovery requirement.

The allow map separates:

- Read-only runtime: installed COMSOL/JDK images, required shared libraries, approved license configuration. Commercial runtime stays local and outside distributed bundles.
- Writable private task storage: home/TMP/cache/output/MPH/checkpoints/progress inside the exact authorized task root and its quota-backed storage. ControlDaemon authority keys, unrelated projects, user data and backups are excluded.
- Network: task control/Worker/owned COMSOL loopback endpoints and, only if actually required, the exact approved license-manager endpoints. General internet/subnet access must not be inferred from a broad network entitlement.
- Processes: owned launch tree and documented required helper launches with inherited identity/policy. No control over foreign user processes or attach to shared servers. Do not claim an executable allowlist if the chosen OS policy cannot enforce it.

## Platform mechanisms and feasibility

macOS candidates must use a documented effective boundary and verify the actual vendor launch tree. Apple documents that App Sandbox configuration is in code signatures, and child sandbox inheritance requires the child target's `app-sandbox` and `inherit` entitlements. Network client/server entitlements grant connection categories rather than a per-endpoint allowlist. See [Apple entitlement reference](https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/EntitlementKeyReference/Chapters/EnablingAppSandbox.html) and [current helper-tool guidance](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app).

**Inference requiring feasibility checks:** a generic sandboxed wrapper does not establish that installed JDK/COMSOL binaries have the necessary inheritance, file access and helper behavior. Start with read-only signature/image/launch inspection and locally owned test binaries. Do not re-sign or modify installed vendor binaries, disable SIP/Gatekeeper, install launch services or enable broad temporary exceptions as an implementation shortcut. If local private runtime copies/signing or a separate isolated deployment are necessary, generate an exact proposal and request that scope after tooling is reviewable. A separate account and ordinary ACLs alone are not a complete arbitrary-path sandbox. The previous deprecated `sandbox-exec` same-UID C probe remains historical and cannot establish a supported production profile or separate-account acceptance.

Windows may implement the documented unpackaged AppContainer launch using a profile/package SID, DACL access and `STARTUPINFOEX/SECURITY_CAPABILITIES`. Inspect actual tokens and descendant inheritance. A restricted token, Job Object or account name alone is not AppContainer proof. See [Microsoft launch API](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer) and [legacy application inheritance](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-for-legacy-applications-). COMSOL/JDK compatibility, license access, loopback exceptions and exact network policy remain to be measured; an OS API example is not native acceptance. Account, DACL, loopback/firewall and storage changes are not currently authorized.

### Read-only Windows observer slice

The current T037 candidate adds a closed v2 profile bridge and a late-bound
read-only SCM/WFP observer. It reads service configuration, service-SID type,
service status/PID and owner/group/DACL security descriptors, samples the
service PID's token/birth/image before and after the WFP snapshot, and gathers
all provider, sublayer and filter pages inside a read-only WFP transaction.
Enumeration is bounded to 256 rows per page, 100,000 rows, 391 pages per kind,
1,173 pages total, 64 MiB canonical snapshot bytes, and 20 seconds. If a page,
row, condition, ABI, or release cannot be verified, it yields typed refusal
or UNVERIFIED; a partial inventory is never called complete. Canonical ACL
digests preserve owner/group, descriptor control flags, DACL state and ordered
exact ACE bytes.

This inventory does not establish effective WFP arbitration or issue a
dispatch binding. The complete layer catalog and all field semantics,
provider-context/callout behavior, rights arbitration, exact task Job handle
and process tree, executable contents, filesystem-grant comparison, and
COMSOL/JDK loopback compatibility remain unresolved. Thus the only result is
read-only observation with policy_binding NOT_ISSUED; injected adapters are
SOFTWARE_ONLY. The launch factory remains fail-closed, and the native Windows
kernel path is NOT_RUN. The v1 profile remains intent-only and is not
implicitly upgraded to v2.

The observer's WFP declarations follow Microsoft's [FWPM_FILTER0 structure
and flags](https://learn.microsoft.com/en-us/windows/win32/api/fwpmtypes/ns-fwpmtypes-fwpm_filter0),
[paged filter enumeration and returned-memory ownership](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmfilterenum0),
[provider structure](https://learn.microsoft.com/en-us/windows/win32/api/fwpmtypes/ns-fwpmtypes-fwpm_provider0),
[sublayer structure](https://learn.microsoft.com/en-us/windows/win32/api/fwpmtypes/ns-fwpmtypes-fwpm_sublayer0),
and the [read-only transaction API](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmtransactionbegin0).
SCM reads use Microsoft's [QueryServiceConfig2W](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-queryserviceconfig2w)
and [QueryServiceObjectSecurity](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-queryserviceobjectsecurity).

The existing host-change plan and OperationStore journal remain software
artifacts only: they bind desired identities, ownership GUIDs, proposed
baseline/backup references and an authorized job/root, but live baseline and
backup bytes are NOT_CAPTURED. No SCM, WFP/firewall, account, ACL, quota or
code-signing mutation is implemented or authorized in this slice. Real
apply/restore, a native proof issuer and a trusted launch connection remain
implementation gaps, not an external permission blocker.

## Resource semantics

Current computation budgets bound cases/scheduler admission; `execution_timeout=null` preserves unlimited engine duration. Current progress producer caps bytes persisted into the OperationStore at1MiB, but `ModelUtil.showProgress(String)` writes a regular file with no verified per-file quota API. Do not describe the read cap as a disk cap, truncate an active writer, invent a FIFO sink, or change global Worker limits affecting unrelated saves.

Implement host-configured disk admission and reservation using real filesystem identity/free-space observations and explicit configured reserve/quota values, not caller values or invented defaults. Refuse before dispatch when the configured budget cannot be verified/satisfied. Serialize reservations through the current durable control/queue mechanism; no new queue. Retain original committed model and recovery storage when budget is insufficient. Report admission-only monitoring separately from enforceable OS backing. Actual hard bounds require a verified private quota-backed filesystem/storage profile covering writable task caches, models and logs; prepare those host changes in the reviewable deployment plan. Continue read/status/cancel/cleanup paths after budget exhaustion. Never use RPC wait expiry to kill an engine. Memory/concurrency budgets and no-progress alarms retain their actual distinct scope; do not claim they are implemented merely because this disk adapter exists.

## Acceptance and evidence

Use production validation/startup/launch adapters with synthetic plan fixtures for software tests: missing/tampered/stale/wrong-host/wrong-root/wrong-PID-birth/profile mismatch, inherited child missing policy, broad/unknown network rights, budget reservation collisions, insufficient/changed storage and interrupted apply/restore. These tests are software-only.

Then, under the approved isolated test account, run a **bounded new** real Worker + owned COMSOL Server activity: allow legitimate model create/read/save and required license access; deny synthetic outside-root file reads/writes, disallowed loopback/network targets, access/control of a synthetic foreign-account process, and repeat the probes in actual child processes and server-side Java external execution. Preserve OS policy/account/token/UID/permissions, exact binaries, network and execution logs, task-owned cleanup, and original denied outcomes. No real private file or third-party network destination is needed for a denial probe. Server-side/descendant proof is required. Runtime failure is FAIL/UNKNOWN, missing approved host conditions are BLOCKED, and absent implementation remains CHANGES_REQUIRED.

W26 installation/offline/full-restore and final independent review remain separate requirements. Profile feasibility is not permission to skip them. Final review must assess exact supported scope and measured limits.
