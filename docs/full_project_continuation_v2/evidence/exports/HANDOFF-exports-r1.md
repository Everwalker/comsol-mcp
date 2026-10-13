# Export handlers handoff (r1)

`_g3_exports.py` now contains callback handlers for `export.report`, `export.evidence_bundle`, and `artifact.publish`, plus strict output data schemas. The two file-producing handlers use the existing project root, ArtifactStore pinning, bounded streaming, no-clobber publication, and `register_project_artifact`. They retain a failure receipt when a post-staging step is uncertain.

The report reads the bound model snapshot and real paginated model tree, durable project/model-bound OperationStore statuses/events, registered schema-v2 project artifacts, and registered W17 observation hashes. The event projection retains the source metadata hash and omits secrets, absolute paths, and unapproved free-form fields. The report labels its scientific acceptance `UNVERIFIED` regardless of the file-integrity checks.

The evidence bundle uses `Model.save(path, true)` through the existing Worker save-copy method by default, checks that the live filepath and available model fingerprint remain unchanged, and streams each included source through a pinned file handle into the ZIP while recomputing SHA-256 and size. It includes the save-copy MPH, explicitly selected registered schema-v2 artifacts, same-ModelRef W17 observations, a redacted durable operation log projection, a report, provenance, and a member manifest. Missing recipe, raw-data, plot, or model-bound log categories produce an explicit `INCOMPLETE` manifest and response. Portability remains `UNVERIFIED`.

`artifact.publish` requires a backend-installed permission checker, project/destination/artifact-scoped verifier returning exactly `True`, and resolver for a registered destination. The provider must exhaust the pinned byte iterator and return a receipt proving exact artifact identity, no-clobber publication, and readback. No provider is installed in the current managed callback context, so production calls currently refuse with `PUBLISH_UNAVAILABLE`. The positive test uses a synthetic local sink only; it does not publish externally.

Backend/registry integration is deliberately pending the Windows owner’s shared-source handoff. The required managed-context seam is:

* `host_permission_check(project_id, "host_control") -> True`
* `host_publish_destination_resolver(project_id, destination_ref) -> {schema, project_id, destination_ref, registration_sha256, publish_chunks}`
* `host_authorization_scope_verifier(project_id, "artifact.publish", exact_scope, authorization_ref) -> True`

The destination provider is responsible for resolving a user-registered host destination and performing safe no-clobber publication/readback; caller-supplied paths are never accepted. This operation does not send messages or default to a network/email destination.

Verification used Python 3.12.13 with the recovered local test runtime. The focused regression set passed **147 tests, 1 skipped, 0 failed**. The skip is `test_artifact_verify_real_accepted_dev1_bundles_through_registration`, because accepted dev1 archive paths were not supplied in this environment. The exact command, JUnit digest, and source digests are in [SOURCE_FREEZE_exports_r1.json](SOURCE_FREEZE_exports_r1.json). COMSOL native execution is `NOT_RUN`; result/physical acceptance and external-host publication are `UNVERIFIED`/`NOT_RUN`.
