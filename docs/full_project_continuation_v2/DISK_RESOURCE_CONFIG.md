# Trusted disk-admission startup configuration

This configuration enables the current POSIX `fstatvfs` admission coordinator. It is not a hard quota, a temporary-file limit, a memory limit, or an OS sandbox. Windows remains unsupported until a native root/storage identity adapter is implemented.

The daemon accepts the same file through the CLI or environment:

```sh
python -m comsol_mcp._control_daemon \
  --home /absolute/private/control-home \
  --disk-resource-config /absolute/private/disk-resource.json
```

Alternatively set `COMSOL_MCP_DISK_RESOURCE_CONFIG` in the daemon's trusted service environment. A CLI path takes precedence over the environment value. If neither is supplied, startup keeps the prior unconfigured behavior. If a path is supplied but cannot be read and validated as a trusted file, startup refuses before opening the listener.

The strict schema is shown below. Replace every example identity with values read from the actual host and the exact existing registered project root; the strings beginning with `REPLACE_` are explanatory placeholders and cannot be loaded as-is. Store the file in a private, ownership-enforcing filesystem: on POSIX the config file must be private, its parent must not be group/other writable, and the pinned root must match its configured device/inode/owner. Darwin `MNT_IGNORE_OWNERSHIP` volumes and unsupported ACL checks are rejected.

```json
{
  "schema": "comsol-mcp.disk-resource-config/v1",
  "host_id": "REPLACE_WITH_EXACT_HOSTNAME",
  "platform": "macos",
  "profiles": [
    {
      "profile_id": "project-disk-admission",
      "project_id": "REPLACE_WITH_REGISTERED_PROJECT_ID",
      "root": {
        "path": "/absolute/canonical/path/to/the/existing/project",
        "volume_id": "REPLACE_WITH_DECIMAL_ST_DEV",
        "file_id": "REPLACE_WITH_DECIMAL_ST_INO",
        "owner_id": "REPLACE_WITH_DECIMAL_ST_UID"
      },
      "storage_id": "REPLACE_WITH_posix_ST_DEV_AND_F_FSID",
      "disk_budget": {
        "mode": "ADMISSION_ONLY",
        "reserve_free_bytes": 1073741824,
        "max_task_reservation_bytes": 5368709120,
        "quota_profile_id": null
      },
      "allowed_operations": ["api.invoke"],
      "trusted_estimates": [
        {"operation_id": "api.invoke", "estimate_bytes": 1048576}
      ]
    }
  ]
}
```

`storage_id` uses the provider's current observed filesystem form, normally `posix:<st_dev>:<f_fsid>` where the platform exposes `f_fsid`, otherwise `posix:<st_dev>`. The root identity uses decimal `st_dev`, `st_ino`, and `st_uid` strings. The current provider securely reopens and pins the root before each admission, checks the exact identity and re-reads free capacity.

Every allowed canonical operation must have exactly one positive trusted estimate no greater than `max_task_reservation_bytes`. The estimate is operator-controlled startup policy, not caller input or a measured upper bound. Existing public routes classified `DYNAMIC`, including `api.invoke`, are conservatively reserved; until a reliable READ-effect resolver is available they also need a trusted estimate. The exact public capability/project permission check still runs before reservation and dispatch. Cached read/status/cancel/cleanup paths keep their existing handling.

Reservations are written to the existing durable OperationStore before the job enters `RUNNING` or sends Worker work. A confirmed terminal result releases the reservation; `UNKNOWN` keeps it held unless a separate fresh quiescence proof is available. This mechanism is advisory free-space admission: it cannot prevent an untrusted or external writer from consuming space after observation, and it does not cap COMSOL temporary files.
