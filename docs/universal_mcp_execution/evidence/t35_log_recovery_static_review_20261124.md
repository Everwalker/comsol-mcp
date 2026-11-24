# T35 bounded event recovery: static review

This review applies to the source at commit `03e249d025e08cce6687c10924aafea4cde52b47`. The existing event-field recovery guide and the `job.log.event.read` description already reflect the implemented behavior.

- A changed event field returns `EVENT_FIELD_CHANGED` with `safe_retry=false`. A new read snapshot starts at offset zero only when the user explicitly requests it; old pages must be discarded.
- Event-field reads distinguish SQLite TEXT, BLOB, and SQL NULL. The documented fallback applies when `blobopen` is unavailable; a `blobopen` operational error is reported separately.
- The guide and both action catalog copies are already present in the reviewed source. This review required no source or documentation change.

The review was static and did not run an MCP service, a COMSOL Worker, or a native target. It does not establish a bound on SQLite internal allocations or whole-process memory, and does not establish complete solver-log recovery.
