# T32: same-server scheduling and lock scope

Status: **open**. The findings below are synthetic and do not establish native COMSOL behavior or product acceptance.

Two independent `ControlDaemon` instances connected through fake Workers to the same in-memory fake Server. Their separate schedulers admitted two conflicting callbacks at once (maximum active count: 2), while `session.inspect`, `job.status`, and `job.log` remained responsive. This fake setup did **not** exercise the Java Worker's endpoint lock, so it does not prove that production Workers admit the same concurrency.

Source review found that a Java Worker acquires a `FileChannel.tryLock()` lock for a resolved endpoint during connection and reports `ENGINE_BUSY` on contention. The protection depends on the Workers seeing the same lock root and canonical endpoint key; it refuses a competing connection rather than establishing FIFO job scheduling. Two independent Java 11 JVMs on one macOS ARM64 host were checked with task-owned test roots: the same root/key yielded `BUSY`, different roots could both acquire, and acquisition succeeded after release. Two equivalent numeric IPv6 spellings produced the same key. No production lock directory, real Worker, Server, MCP service, or network connection was used.

These results do not verify real COMSOL connection lifecycle, another OS user, another machine or filesystem, different endpoint aliases, third-party clients, FIFO ordering, or Server-side fencing. Cross-daemon coordination and UNKNOWN-owner reconciliation remain unresolved. T32/U07 and native-target checks remain open.
