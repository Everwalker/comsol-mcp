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
assessment succeeds. The current adapter can read process tokens, process
birth/image, supplied Job Object state, volume capacity, and ACL metadata, but
cannot verify SCM service identity, WFP policy, COMSOL loopback compatibility,
or the full descendant boundary. Therefore the current policy state remains
`UNVERIFIED`; no AppContainer process is launched. Server and Worker factories
share a per-session Job handle and have no fallback to `subprocess.Popen` when
selected, but no verifier currently issues the proof required to reach that
launch path.

`require_hard_quota` is only an explicit requirement marker. This adapter does
not query or apply hard quotas. Job Object committed-memory limits, disk
admission, filesystem quotas, and OS isolation are separate controls. The
read-only feasibility command is `python -m comsol_mcp._windows_t037`; its
report never includes raw account SIDs or credentials. Host policy apply and
restore remain plan-only and require a separate reviewed implementation and
authorization.
