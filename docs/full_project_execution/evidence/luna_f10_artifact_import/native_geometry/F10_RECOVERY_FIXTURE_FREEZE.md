# F10 new-project artifact-to-geometry recovery freeze

Status: frozen for the next authorized native run; this candidate has not yet
started a COMSOL engine.

This is a narrowly scoped recovery run after the former task-owned temporary
project and OperationStore were verified missing. It creates a fresh
task-specific project root and OperationStore. It does not recreate or edit
the missing SQLite database. The run uses the protected COMSOL-native MPHTXT
source produced and read back in the earlier successful source-geometry action:

- Source: `/private/tmp/comsol-mcp-native-artifact-geometry-source-20260926T2226Z/inputs/rectangle_2x1.mphtxt`.
- Length: 1062 bytes.
- SHA-256: `a2a88b2a0859661c69a81598903bed6717753d0cf9178b9f5a697797fa75551e`.
- Durable SSD copy and original Worker action/receipt are independently
  hash-bound in `source_recovery_20260926T2226Z/input_source_receipt.json`.

The frozen action sequence uses one task-owned COMSOL 6.4.0.293 loopback
engine and a new project id `native-artifact-geometry-recovery-20260926t2226z`:

1. Copy the verified MPHTXT into the new project and check the original source,
   project input, and protected SSD copy are identical before registration.
2. Call the production `artifact.register` action exactly once, with role
   `geometry_source` and classification `internal`.
3. Read back the durable OperationStore row, managed content-addressed copy,
   size, project id, artifact id, and SHA-256. Recheck source and managed-copy
   bytes after registration.
4. Import that registered artifact into a fresh target model, build geometry,
   and read back its dimensions, domains, bounds, area, and unit.
5. Close the Worker and owned engine only after the project is idle; preserve
   process birth, listener, action, store, readback, failure, and cleanup
   evidence.

Frozen limits are one engine start, at most 600 seconds from the actual engine
PID birth (`lstart`) through native actions and cleanup, exactly one new
production registration, zero native exports in this run, zero `study.run`
calls, and zero solver calls. `study.run` is guarded in the production dispatch
path. Geometry-sequence `run()` is allowed only to build the target geometry.
The runner shortens each bounded RPC timeout as the deadline approaches and
reserves the final 20 seconds for exact-owner cleanup.
No physics, mesh, study, solver, export, Desktop session, remote client, or
external CAD input is part of this run. If any gate fails, the result remains
`FAIL_OR_INCOMPLETE`; no partial readback or offline test is native acceptance.

Target geometry values are fixed before native execution:

The source fixture is a 2-D rectangle with size `(2 m, 1 m)`.

- Source rectangle size: `(2 m, 1 m)`.
- Space dimension: 2.
- Domain count: exactly 1; entity count in dimension 2 is 1.
- Bounds: `[0, 2, 0, 1] m`, maximum absolute coordinate error `1e-10 m`.
- Area: `2 m^2`, absolute error at most `1e-8 m^2`.
- Geometry length unit: `m`; import type: native MPHTXT; build: true.

The former run's verified source readback is provenance for these input bytes;
it does not establish that the new project registration or import succeeds.
The run-specific `fixture_freeze.json` binds current source-code hashes,
receipt hash, source hash, engine target, process inventory, and this budget
before the new COMSOL server starts.

Version-specific API references are retained with the runner's frozen
evidence manifest: `GeomSequence.exportFinal(String)` source provenance, native
MPHTXT `Import` support, `GeomInfo` geometry readback, and measurement methods
from the local COMSOL 6.4.0.293 documentation. Native export is provenance only
in this recovery run; no export action is allowed here.
