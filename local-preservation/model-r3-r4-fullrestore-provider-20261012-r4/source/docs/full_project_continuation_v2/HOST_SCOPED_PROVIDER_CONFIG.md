# Host-scoped authorization and local publish configuration

The control daemon can load administrator-managed scoped grants and project-local publish destinations at startup. The file is a host capability input: there is no MCP operation that creates destinations or grants. Its JSON schema is `comsol-mcp.host-scoped-provider-config/1` and its only top-level fields are `schema`, `destinations`, and `grants`.

Keep the file in a daemon-owned directory with mode `0700`, and the file itself at mode `0600`. The loader opens it without following a symlink, bounds it to 1 MiB, rejects duplicate keys and unknown fields, pins the file identity, and rechecks its identity and SHA-256 before each authorization or publish. Changing or replacing the file while the daemon is running makes authorization fail closed; restart the daemon to install a new configuration. The registered local sink must be a daemon-owned private directory (mode `0700`).

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

The host administrator computes the digests with `scope_sha256(project_id, action, scope)` and `destination_registration_sha256(project_id, destination_ref, sink_root)` from `comsol_mcp._host_scoped_provider`. A publish scope includes the destination registration digest plus the exact sorted artifact IDs, SHA-256 values, and byte sizes. A `model.close` scope includes the complete current ModelRef and expected revision. `authorization_ref_sha256` is the SHA-256 of the one-time reference the host later supplies with that exact request; the raw reference must never be written to this file. Grants expire and cannot exceed one year from configuration load.

For example, start the daemon with:

```sh
python -m comsol_mcp._control_daemon \
  --home /absolute/path/to/private/control-home \
  --host-provider-config /absolute/path/to/private/host-provider.json
```

`COMSOL_MCP_HOST_PROVIDER_CONFIG` is also accepted by the existing daemon startup path. The local sink uses ArtifactStore's pinned, streaming, no-clobber publication and verifies the written SHA-256 and size. It does not send data over a network. Missing configuration, expired grants, wrong actions/scopes, changed config, and changed sink identity remain typed refusals.

The local software tests exercise the configured ControlDaemon dispatch path with synthetic project data only. They do not establish Windows ACL behavior, COMSOL-native behavior, or authorization for an external delivery service.
