# W24 R16 selection-trace overflow repair (isolated source candidate)

R16 R2’s actual `baseline/step` setup call failed with `EXECUTION_STATE_UNKNOWN`, `safe_retry=false`, and Worker `FAILED`. The copied raw route response reports the deepest observed exception as `IllegalStateException: selection trace exceeded its fixed safe envelope`. The copied step trace has exactly 1,024 rows; row 1,024 is the `before` breadcrumb for `solver.sol1.feature.t1.i1.resolve`. The successful `baseline/flat` trace has 957 rows and ends after the final time-solver readback.

The frozen Java `SelectionTrace.call()` records `before`, runs the API, then records `after`; an API exception is recorded through `recordFailure`. With no reserved terminal record, the 1,024-event limit could throw while writing either `after` or an error breadcrumb. The actual outcome of the API for the last `before` event is therefore **UNOBSERVED**. This repair does not reinterpret the original UNKNOWN or claim whether COMSOL succeeded or failed at that call.

The isolated repair raises the still-fixed event cap to 4,096 and preflights two available rows before dispatching each traced API call. Both Java mirrors and the exact `SOURCE_ORIGINS.json` metadata are synchronized. Existing private fresh-file, stage-length, and fixed-event guards remain. Physics, numerical thresholds, model topology, ownership, and execution gates are unchanged.

The focused selection-diagnostic, actual-output regression, and existing fixture/provenance controls pass **18/18, zero skips**. They pin the actual R16 route failure and both copied traces by SHA-256, include the successful flat trace control, verify preflight order and source mirror/origins, and reject tampered response/trace evidence.

The R16 R2 source freeze and its consumed once are preserved. The old activity remains `FAILED_UNKNOWN`; no retry or RPC occurred. `javac`, Java, and COMSOL were not run under the current no-JVM/native instruction, so compile and fresh-native confirmation remain **NOT_RUN**.
