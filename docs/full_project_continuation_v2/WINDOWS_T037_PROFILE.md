# Windows T037 profile input

`COMSOL_MCP_WINDOWS_T037_PROFILE` or `--windows-t037-profile` selects one
trusted local JSON profile when starting the control daemon. The file is
loaded through a pinned Windows path and the private-path ACL verifier. The
profile records intent and host binding; it is not an OS policy receipt and
cannot mark itself verified.

The closed `comsol-mcp.windows-t037-profile/1` input has this shape:

```json
{
  "schema": "comsol-mcp.windows-t037-profile/1",
  "profile_id": "comsol-isolated-session",
  "host_fingerprint_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "service_account_sid": "S-1-5-21-1000",
  "interactive_account_sid": "S-1-5-21-1001",
  "appcontainer_sid": "S-1-15-2-123456789",
  "capability_sids": ["S-1-15-3-123456789"],
  "server_image_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "worker_image_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "workspace_root": "C:\\COMSOL\\private-workspace",
  "require_hard_quota": false
}
```

All example digests and SIDs are placeholders. The service and interactive
account SIDs must differ. The AppContainer SID and capability SIDs must
already exist; startup does not create an account, profile, ACL, firewall/WFP
rule, quota, or Job Object limit. The profile path must be on a local volume
with persistent ACL support and pass the daemon-owner/DACL verifier.

When a profile is configured, T037 session birth/connect and `TRUSTED_CODE`
operations are refused before durable admission until a fresh native policy
assessment succeeds. The v1 adapter is intent-only and is never upgraded
implicitly. The separate closed comsol-mcp.windows-t037-profile/2 wrapper
embeds one complete shared HostResourceProfile with a windows_isolation
object. That object records the exact SCM service key and start name, distinct
service-logon SID and service SID, restricted service-SID mode, AppContainer
SID/capabilities, exact launcher/server/worker image and argv digests, exact
filesystem ACL grant digests, WFP provider/sublayer/filter GUIDs, and
per-image/per-principal network tuples (direction, ALE layer GUID, IPv4/IPv6,
protocol, local and remote CIDRs and ports). Service-logon and service SIDs
are separate fields; neither can be inferred from a display name or from the
other. The shared schema parser rejects unknown keys and malformed selectors.

The argv digest is SHA-256 of the UTF-8 encoding of the compact canonical JSON
array returned by Windows CommandLineToArgvW: json.dumps(argv,
ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).
The native observer normalizes image paths with ntpath.normcase and
ntpath.normpath before comparing them.

The current observer makes read-only calls. It samples SCM configuration,
service-SID type, service status/PID and owner/group/DACL security descriptor;
it reads the service PID's token, process birth time and image on both sides
of the policy read; and it enumerates WFP providers, sublayers and filters in
a read-only transaction. Every WFP page is consumed (page size 256), with
limits of 100,000 rows, 391 pages per object kind, 1,173 total pages, 64 MiB
of canonical snapshot data and 20 seconds total. A limit, ABI mismatch,
unknown row/condition, query error, or cleanup failure refuses to produce a
complete snapshot. The WFP snapshot records page/row/byte/time counts and
canonical owner/group/DACL hashes for each observed object. Security
descriptor normalization retains ordered exact ACE bytes and distinguishes
absent from null DACLs.

A complete read-only inventory still does not establish effective isolation.
The observer cannot yet compare project ACL grants to native descriptors,
obtain the exact session Job handle/process tree, prove loaded executable
contents, interpret all WFP layers/conditions/provider contexts/callouts and
rights, or verify COMSOL/JDK loopback compatibility. Its result is always
UNVERIFIED, with policy_binding: NOT_ISSUED; injected adapters are
SOFTWARE_ONLY and cannot issue a native binding. Server and Worker factories
remain fail-closed before process creation. No AppContainer or host policy is
created or changed by this observer.

`require_hard_quota` is only an explicit requirement marker. This adapter does
not query or apply hard quotas. Job Object committed-memory limits, disk
admission, filesystem quotas, and OS isolation are separate controls. The
read-only feasibility command is `python -m comsol_mcp._windows_t037`; its
report never includes raw account SIDs or credentials. Host-control plans are
closed, fingerprinted and durably journaled against an authorized
OperationStore job, but baseline/backup observations are explicitly
NOT_CAPTURED and all journal evidence is SOFTWARE_ONLY. Host policy apply and
restore remain unimplemented; no service, account, ACL, WFP/firewall, quota,
or code-signing change was made.

Native Windows execution for this unit is NOT_RUN. The observer's compiled
structure layouts use fixed-width Win32 integer aliases and explicit 64-bit
size/offset checks, but the running kernel/SCM/WFP ABI still needs native
Windows tests. See Microsoft's [FWPM_FILTER0 layout and flags](https://learn.microsoft.com/en-us/windows/win32/api/fwpmtypes/ns-fwpmtypes-fwpm_filter0),
[paged filter enumeration](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmfilterenum0),
[FWPM_PROVIDER0](https://learn.microsoft.com/en-us/windows/win32/api/fwpmtypes/ns-fwpmtypes-fwpm_provider0),
[FWPM_SUBLAYER0](https://learn.microsoft.com/en-us/windows/win32/api/fwpmtypes/ns-fwpmtypes-fwpm_sublayer0),
[service configuration query](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-queryserviceconfig2w),
and [service-object security query](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-queryserviceobjectsecurity).
WFP ACL reads use Microsoft's [`FwpmFilterGetSecurityInfoByKey0`](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmfiltergetsecurityinfobykey0)
family (filter/provider/sublayer); returned descriptor storage is freed with
[`FwpmFreeMemory0`](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmfreememory0).
