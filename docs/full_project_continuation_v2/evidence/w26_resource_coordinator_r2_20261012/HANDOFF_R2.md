# W26 resource coordinator handoff r2

The current W26 resource coordinator candidate has exact source copies and SHA-256s in `FREEZE_R2.json`. It is scoped software evidence, pending Root review. The W26 suite passed 35/35 and the affected existing ControlDaemon suites passed 147/147 under the strict network guard; raw JUnit, stdout/stderr, and guard logs are referenced in the receipt.

The production-path controls cover reservation before RUNNING/Worker dispatch, sufficient-budget dispatch and terminal release, insufficient/competing/wrong-root refusal before dispatch, UNKNOWN retention through restart, and queued-only crash reconciliation without replay.

This does not prove native Windows support, hard disk quota, memory limits, COMSOL behavior, host isolation, or T037 acceptance. POSIX `fstatvfs` is advisory admission only. Windows native/runtime, real quota, process/file/network policy and apply/restore remain unavailable or NOT_RUN.
