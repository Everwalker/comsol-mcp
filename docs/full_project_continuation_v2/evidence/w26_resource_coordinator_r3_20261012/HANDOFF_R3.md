# W26 resource coordinator r3 handoff

This is a scoped software correction, not final acceptance. Freeze fingerprint: `72be4f3422528fde9e02227ab3eaac81be6c2108f06760415e2a00140abf1f03`. The r2 frozen source and Root `CHANGES_REQUIRED` review remain preserved. This r3 adds the startup/CLI/environment route for the trusted disk configuration and closes the catalog `DYNAMIC` admission bypass conservatively.

The daemon now accepts `--disk-resource-config` or `COMSOL_MCP_DISK_RESOURCE_CONFIG`; invalid configured data is rejected before listener startup, while no disk config preserves the old startup behavior. The config shape and actual fields are documented in [DISK_RESOURCE_CONFIG.md](../../DISK_RESOURCE_CONFIG.md).

Configured `DYNAMIC` operations require a project-bound profile and a trusted per-operation estimate. Software controls confirm the real `api.invoke` public `comments(String)` route refuses low budget before any Worker send and, with adequate budget, completes one durable operation then releases only after terminal status. Resolver controls cover `api.invoke`, `result.numerical_manage`, `result.table_manage`, and `plot.view_manage`.

Actual guarded regression: W26 `45/45`; ControlDaemon/project/session `147/147`; frozen host-provider compatibility `27/27`. All three guard logs contain only activation-before-test events and no denials. Earlier focused schema/isolation debug JUnit files remain in this folder and are marked debug-only in `FREEZE_R3.json`.

POSIX capacity remains admission-only. Windows kernel/storage, hard quota, memory cap, actual COMSOL/native science and T037 OS isolation remain `NOT_RUN`, unsupported, or open as declared in the freeze manifest. This handoff is not an independent final Reviewer signoff.
