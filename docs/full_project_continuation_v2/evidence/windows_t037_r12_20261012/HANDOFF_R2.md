# Windows T037 r2 handoff

This r2 snapshot preserves the earlier r1 bytes and adds one narrow security correction: no mutable same-process `_proof` field can unlock launch. `T037SessionProcessFactory.preflight()` now returns typed `T037_POLICY_UNVERIFIED` until a trusted native policy verifier and launch-proof issuer are implemented. The regression sets `factory._proof = object()` and checks that the refusal occurs before any native API or process fallback.

The code contains a read-only Win32 observation adapter and a suspended AppContainer/Job launch ABI path wired to the existing owned Server and Java Worker seams. The actual Windows kernel was not exercised. Because the policy verifier/issuer does not exist, the factory refuses process launch on all hosts; this is intentional fail-closed behavior, not a T037-complete result.

The strict guarded focused suite passed 24 tests. The guard recorded one active-entry event and zero denied operations. `py_compile` and scoped `git diff --check` passed. Earlier r6 (25 passed, one deselected) and r7 (8 passed, 49 deselected; local task-only loopback fixtures) remain bound to unchanged source inputs; r4's diagnostic failures and guard-denied attempts remain preserved and are not represented as product acceptance.

Still `CHANGES_REQUIRED` or `UNVERIFIED`: native Windows ABI/runtime proof, actual AppContainer identity, service/SCM identity installation, WFP/network policy query/apply/restore, loopback and vendor COMSOL compatibility, descendant/job accounting, disk hard quota and memory hard caps. No OS policy/account/ACL/firewall changes were applied. No COMSOL or scientific acceptance was run.

R1 remains immutable at `local-preservation/windows-t037-20261012-r1/`; this r2 snapshot is `local-preservation/windows-t037-20261012-r2/`. Exact source inputs, dependency hashes, JUnit, strict guard log, and the two-file delta are recorded alongside this handoff.
