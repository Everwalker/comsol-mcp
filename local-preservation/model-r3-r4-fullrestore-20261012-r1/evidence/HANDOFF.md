# Delivery, export, and full-restore software closure

The current source/test closure is bound by [`SOFTWARE_CLOSURE.json`](SOFTWARE_CLOSURE.json), SHA-256 `e0e8df7525ef54184e18326e4813828347b16b2ca75d00cd38eb8d40051f7b2a`. It contains SHA-256 values for 40 affected source and test files and both raw JUnit reports.

The affected software regression passed: 250 tests, 20 subtests, 10 skips, 0 failures, 0 errors. Raw JUnit: [`junit-affected.xml`](junit-affected.xml), SHA-256 `f7775a93e2ab4d1c687a2cee232e9aa5f2b18a3a2b32d57ba5de573df48ee45e`.

The elevated software Worker loopback regression passed: 37 tests, 1 skip, 0 failures, 0 errors. Raw JUnit: [`junit-java-worker.xml`](junit-java-worker.xml), SHA-256 `a534f930a67683cd9032580443c57f9479de3fa018927e66c42b8f37bed22b49`. The first unprivileged attempt was denied at loopback bind with `BindException: Operation not permitted`; the authorized retry bound ephemeral loopback only. It did not start a COMSOL server or run a solve and did not touch ports 80752/80851.

The skips are one optional accepted-dev1 archive fixture whose historical archive paths were not supplied, nine cases that explicitly require a Windows kernel/filesystem, and one Worker test for Windows owner semantics. COMSOL native execution, Windows kernel behavior, external delivery, cross-machine portability, and final review remain `NOT_RUN`.

Covered paths include model save overwrite recovery and no-clobber, durable model/recipe lifecycle routes, `model.package` followed by `artifact.verify`, ControlDaemon-bound report/bundle/publish and scoped host authorization, and synthetic full-database plus project/artifact backup/restore with foreign-directory, parent-symlink, journal-fence, and rollback controls. The full restore did not reactivate historical jobs or Worker/ModelRef bindings.
