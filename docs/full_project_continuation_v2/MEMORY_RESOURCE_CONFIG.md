# Host memory admission configuration

Memory admission is an advisory, host-wide pre-dispatch reservation. It is independent of disk admission and of T037 OS isolation. The Darwin observer uses `host_statistics64(HOST_VM_INFO64)` and a boot-time sysctl; its available value is the conservative free-page count multiplied by host page size. The Windows observer uses `GlobalMemoryStatusEx.ullAvailPhys`; its boot binding is an uptime/UTC estimate. Neither observer enforces a hard limit. Receipts therefore retain `hard_cap_state: NOT_OBSERVED`.

Configure a private startup file through the daemon CLI or service environment:

```sh
python -m comsol_mcp._control_daemon \
  --home /absolute/private/control-home \
  --memory-resource-config /absolute/private/memory-resource.json
```

The equivalent service variable is `COMSOL_MCP_MEMORY_RESOURCE_CONFIG`. The CLI argument takes precedence. Without either value the existing no-profile behavior remains unchanged. A supplied but invalid, untrusted, wrong-host, unsupported-platform, or unreadable profile prevents configured memory admission from starting.

The closed JSON schema is:

```json
{
  "schema": "comsol-mcp.memory-resource-profile/v1",
  "profile_id": "solver-memory-admission",
  "host_id": "REPLACE_WITH_OS_DERIVED_HOST_SHA256",
  "platform": "macos",
  "budget": {
    "max_total_reserved_bytes": 17179869184,
    "reserve_available_bytes": 4294967296,
    "max_task_reservation_bytes": 8589934592
  },
  "allowed_operations": ["study.run", "mesh.convergence_study"],
  "trusted_estimates": [
    {"operation_id": "study.run", "estimate_bytes": 2147483648},
    {"operation_id": "mesh.convergence_study", "estimate_bytes": 4294967296}
  ]
}
```

Replace the example host and estimates with values chosen for the actual machine and workload. Every operation covered by the configured admission effect set must have exactly one estimate; a missing estimate fails closed. Estimates are trusted startup policy, not request fields and not measured upper bounds. The profile file must use the host's private, ownership/ACL-verifiable filesystem. The daemon binds it to a native OS-derived host fingerprint at startup.

Before a job enters `RUNNING` or sends Worker work, the daemon obtains a fresh native observation, validates the durable job/project/operation and permission, and writes the reservation in the existing OperationStore transaction. If both disk and memory are configured, both reservation events commit or roll back together. The current single execution lane remains the only dispatch queue. Confirmed durable terminal states release reservations; `UNKNOWN`/`RECONCILING` remain held across process restart until a separate quiescence mechanism authorizes release. Active reservations are host-wide and count across projects.

This is admission accounting only. It does not limit COMSOL temporary-file growth, process address space, resident memory, descendants, or kernel commit. Hard memory limits, Windows Job Object commit limits, Darwin resource limits, and OS policy application/verification remain separate capabilities and are not represented by this profile.
