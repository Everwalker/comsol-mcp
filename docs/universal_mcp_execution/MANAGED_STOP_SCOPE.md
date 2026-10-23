# Prospective managed-Server stop scope

This note documents a source/software-only authorization path for a **future** Windows SCM-managed Server lifetime. It does not issue an authorization reference, install provider configuration, or establish native Windows acceptance.

The existing host-scoped provider can normalize a `session.stop` class with exactly these fields:

```json
{
  "schema": "comsol-mcp-managed-stop-scope/1",
  "runtime_id": {"id": "<trusted runtime id>", "class": "<qualified runtime class>"},
  "platform": "windows",
  "ownership_mode": "originalManagedLifetime",
  "host_source": {
    "t037_source_sha256": "<trusted T037 source digest>",
    "host_resource_profile_sha256": "<trusted HostResourceProfile digest>",
    "host_id": "<trusted host id>"
  }
}
```

The existing canonical digest helper includes the project id and action. The values come from the retained original daemon/SCM/T037 chain, never request predicates. The class intentionally excludes session ids, PIDs, births and random preference paths. A digest is only a configuration-authoring value: this change adds no issuer, reference-minting operation or approval. `HostScopedProvider.__call__` stays false, and `project.policy_set` cannot consume this scope.

When exact `HostScopedProvider` mode is configured, `session.start` first persists its normal operation/job identity and resolves the trusted runtime and original T037 factory. The daemon retains a sealed root before entering the launcher's process-creation call. The launcher hands back ordered `PRE_CREATE`, exact `CHILD_HANDLE`, and `READY` observations. The root pins the original daemon, project/session/start request, runtime, launcher, factory, SCM runtime/binding, provider, source/profile identity, acquired Job/API, child and handle. Missing or mismatched handoff leaves the normal start uncertain and does not enroll a later handle. Existing public handles, Store rows, PIDs and ordinary `Popen` objects cannot be adopted as origins.

A fresh stop retains normal Store idempotency and lifecycle guards. A cached response or pending result does not require fresh effect authority. Before a new stop effect, the daemon requires current `host_control`, a well-formed reference, the exact retained origin and handle, current trusted source/provider identity and a currently valid exact `session.stop` scope. It writes only authorization/source digests to the audit event, then follows the existing ownership, retired-client, quiescence, other-binding and stop/reap path. No provider or Store lock is held while the blocking stop runs. Scoped authorization errors never fall back to `__call__`, a missing verifier or a legacy lane. Explicitly injected legacy-verifier mode outside exact `HostScopedProvider` mode keeps its existing behavior.

The pre-existing never-resumed native-child cleanup remains a separate exact-handle route. It requires that the original root received the acquired child/handle handoff; it cannot reconstruct authority from a lifecycle row. Roots remain retained after successful mapping removal or uncertain stop. These checks do not create an OS sandbox and do not change SCM, Job, native child, ACL, WFP or Windows permission behavior.

## Evidence boundary

The accompanying `MANAGED_STOP_ORIGIN.json` binds the implementation and test receipts to the fixed `ed09e598...` source archive plus only the leased files listed there. Tests use explicit software-only SCM/process/listener seams and real daemon dispatch, operation storage, provider validation and lifecycle guards. They do not invoke Windows SCM, Windows SDK/native APIs, COMSOL, Java, a real grant/ref, or any existing runtime. Full Windows/native acceptance remains `NOT_RUN` and requires Main review followed by a separately authorized native stage.

## Bounded local software test scope

For the authorized macOS software regression only, the process boundary permits ordinary local CPython fixture children created and observed through their original test-owned Popen handles, plus test servers bound only to 127.0.0.1 or private Unix sockets. This supports process-birth/listener and lifecycle fixtures; it does not permit external network access, process discovery/adoption, old runtime access, or changes to global services, permissions, network, or security settings. The complete run used TMPDIR=/private/tmp and pytest --basetemp=/private/tmp/ms07; HOME and CODEX_HOME were left unchanged.

The positive provenance case tests/test_managed_stop_origin.py::test_original_scm_factory_birth_and_normal_stop_share_one_exact_origin runs actual ControlDaemon.dispatch, Store/idempotency, production origin construction and launcher handoff, WindowsScmServiceBroker._for_software_test(...).run(), and the normal scoped stop path. It does not patch origin creation or validation into success. It injects the explicit software seams for trusted-profile loading, SCM API, process/Job creation and process/listener observations. This is full-path software evidence; it is not a real Windows SCM or native acceptance claim.



Current Main-accepted integration: exact c13047140574a9dc3144dc3c49102c8cbfd1686f plus eight reviewed Stop overlays, 699 input files, manifest 9d099777760ce8483538f7762126d6221abd011c3af2982217a6934813968c38. One coherent software run: 1676 PASS, zero failures/errors/skips/deselections; old1366+(Stop115-overlap16)+catalog148+current-client63, original Main nonWindows counter once. Actual execution-time source-loader/rewrite byte hashes and module frames were captured (127+45 pairs); this is not CodeType or branch coverage. Main independently matches691 nonoverlay Git blobs to actual c130 tree. Auxiliary prior-tar a02a pointer remains UNVERIFIED and is not the source acceptance basis. Original tests and prior failures are preserved; two old Main-only client counters were not rerun in this union. No Windows/native/real grant/COMSOL or final Reviewer acceptance. Detailed current scope and original failed attempts: _state/universal-windows-profile-integration/managed-stop-c130-20261023T042119Z-d439a4/MANAGED_STOP_SCOPE.md. Main review and compact evidence identify exact raw files and inputs.

Publication normalization removes exactly one final LF from _managed_stop_origin.py (tested14550737… to published72cc244e…), with identical AST. Frozen1676 inputs and raw evidence retain original bytes; no test rerun is inferred.
