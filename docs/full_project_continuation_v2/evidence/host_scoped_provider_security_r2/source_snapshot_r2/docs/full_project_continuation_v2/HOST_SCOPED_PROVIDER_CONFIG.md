# Host-scoped authorization and local publish configuration

The control daemon can load administrator-managed scoped grants and project-local publish destinations at startup. The file is a host capability input: there is no MCP operation that creates destinations or grants. Its JSON schema is `comsol-mcp.host-scoped-provider-config/1` and its only top-level fields are `schema`, `destinations`, and `grants`.

Keep the file in a daemon-owned directory with mode `0700`, and the file itself at mode `0600`. The loader opens it without following a symlink, bounds it to 1 MiB, rejects duplicate keys and unknown fields, pins the file identity, and rechecks its identity and SHA-256 before each authorization or publish. Changing or replacing the file while the daemon is running makes authorization fail closed; restart the daemon to install a new configuration. The registered local sink must be a daemon-owned private directory (mode `0700`).

The host-security check is platform-specific. POSIX hosts require the daemon UID and no group/other permission bits. On macOS, the provider reads `f_flags` with `fstatfs` using the local SDK `sys/mount.h` ABI and rejects `MNT_IGNORE_OWNERSHIP`; it also calls `acl_get_fd_np` on each pinned config, parent, and sink descriptor with the SDK's `ACL_TYPE_EXTENDED` value. Any returned ACL entry is refused, and ACL lookup/decoding errors other than the SDK-observed no-ACL `ENOENT` state are unknown and refused. A volume mounted with `noowners` cannot hold the provider file, its immediate private parent, or a registered sink. Windows uses component-relative, no-reparse handles and read-only `READ_CONTROL` queries. It requires a volume advertising persistent ACL support, daemon-SID ownership for the provider file/private parent/sink, and basic unconditional ACEs whose allow principals are limited to the daemon SID, LocalSystem, and Builtin Administrators. Null DACLs, other allow principals, conditional/object/unknown ACEs, and descriptor or identity changes are rejected. The verifier never changes ACLs, account ownership, or mount settings.

`verify_private_path_security(path, directory=..., expected_identity=...)` in `comsol_mcp._host_scoped_provider` exposes the same fail-closed check for other host-owned startup configuration. It returns a pinned-object identity and security-descriptor digest where the platform provides one; callers must retain and revalidate their own handles for subsequent operations. The current macOS software tests exercise an actual private `/private/tmp` path with no extended ACL, a fixture-only extended-ACL refusal, noowners mount refusal, unknown ACL-query refusal, and permission-change refusal. Windows ACL ABI/policy negative controls are software-only; real Windows kernel behavior remains unverified until run on a Windows host.

An example configuration is:

```json
{
  "schema": "comsol-mcp.host-scoped-provider-config/1",
  "destinations": [
    {
      "project_id": "PROJECT_ID_FROM_PROJECT_CREATE",
      "destination_ref": "review-drop",
      "sink_root": "/absolute/path/to/private/review-drop"
    }
  ],
  "grants": [
    {
      "project_id": "PROJECT_ID_FROM_PROJECT_CREATE",
      "action": "artifact.publish",
      "authorization_ref_sha256": "64-lowercase-hex-digits",
      "scope_sha256": "64-lowercase-hex-digits",
      "expires_at_utc": "2026-10-13T00:00:00Z"
    }
  ]
}
```

The host administrator computes the digests with `scope_sha256(project_id, action, scope)` and `destination_registration_sha256(project_id, destination_ref, sink_root)` from `comsol_mcp._host_scoped_provider`. A publish scope includes the destination registration digest plus the exact sorted artifact IDs, SHA-256 values, and byte sizes. A `model.close` scope includes the complete current ModelRef and expected revision. `authorization_ref_sha256` is the SHA-256 of the one-time reference the host later supplies with that exact request; the raw reference must never be written to this file. Grant matching scans the bounded grant inventory and constant-time compares both secret-derived digests. Grants expire and cannot exceed one year from configuration load.

For example, start the daemon with:

```sh
python -m comsol_mcp._control_daemon \
  --home /absolute/path/to/private/control-home \
  --host-provider-config /absolute/path/to/private/host-provider.json
```

`COMSOL_MCP_HOST_PROVIDER_CONFIG` is also accepted by the existing daemon startup path. The local sink uses ArtifactStore's pinned, streaming, no-clobber publication and verifies the written SHA-256 and size. It does not send data over a network. Missing configuration, expired grants, wrong actions/scopes, changed config, and changed sink identity remain typed refusals.

The local software tests exercise the configured ControlDaemon dispatch path with synthetic project data only. They do not establish Windows ACL behavior, COMSOL-native behavior, or authorization for an external delivery service.
