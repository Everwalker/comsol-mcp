# Windows SCM recipe /4: original managed Stop authoring

This is pure source-side authoring inside the original Bootstrap four-file
compiler. It does not issue an authorization reference, install a grant, start
a service, qualify T037, or authorize stopping an existing runtime. Actual
Windows provisioning, SYSTEM loader qualification, MIC enforcement, AppContainer
isolation and COMSOL execution remain outside this software unit.

The source baseline is `e4daac8f35cadd6fd6776466ef2e964aef43a36f`. Recipe
`comsol-mcp.windows-scm-config-recipe/3` and its literal grant behavior remain
available. The accepted Bootstrap, native companion and provider are unchanged.
Only `comsol_mcp/_windows_scm_config_materialization.py` implements the new
`comsol-mcp.windows-scm-config-recipe/4` branch.

## Closed input

Recipe /4 uses the existing Bootstrap V2 `provisioning` field, four templates,
original approval hash, root/SID/SD slots and operator-approved node policy.
At most one grant at
`templates["profiles/host-provider.json"].grants[i].scope_sha256` may be:

```json
{
  "schema": "comsol-mcp.windows-original-managed-stop-grant/1",
  "platform": "windows",
  "ownership_mode": "originalManagedLifetime",
  "source": "FINAL_TRUSTED_PROFILE",
  "runtime_id": {
    "id": "operator-approved-runtime-id",
    "class": "comsol_mcp._session_context.SessionRuntimeConfig"
  }
}
```

This marker is an authoring selector, not a trusted runtime object. Its runtime
id must satisfy the published provider identifier grammar. The surrounding row
keeps exactly the normal provider keys: `project_id`, `action`,
`authorization_ref_sha256`, `scope_sha256`, `expires_at_utc`. Project equals the
final Host project; action equals `session.stop`; reference hash is an approved
literal lowercase SHA-256; expiry is the approved canonical absolute UTC value.
No secret reference is generated or included in evidence.

Unknown fields, other marker positions/versions/domains/classes/sources,
callbacks, supplied host/provider/source hashes, unresolved slots, multiple
typed rows and a typed/literal conflict with the same project/action/ref hash
are rejected. Other approved literal grants retain their existing rules.
Recipe /3 rejects this marker. Normal emitted provider configuration continues
to accept literal scope hashes only; its schema is unchanged.

## Final-byte derivation

Intent validation uses the existing discarded root/service projection for
schema relations. It neither computes nor returns a provisional Stop digest.
The original approval/raw recipe must remain unchanged through compilation.

After the original SDK-owned new/existing root identity, stopped-service SID
and target descriptors are known, compilation proceeds in this order:

1. Substitute only the existing approved slots and canonicalize the final
   trusted-profile bytes once as **B**.
2. Set **R = SHA256(B)**. Parse the exact B through the production T037 v4
   document parser, using R as `source_sha256`, to obtain Host **H**.
3. Construct the published `comsol-mcp-managed-stop-scope/1` with the approved
   runtime id/class, Windows/originalManagedLifetime, and
   `host_source = {t037_source_sha256: R,
   host_resource_profile_sha256: H.sha256, host_id: H.host_id}`.
4. Call the unchanged provider's
   `scope_sha256(H.project_id, "session.stop", scope)`. Replace only the approved
   marker with that literal digest. Validate the ordinary provider document.
5. Freeze B and the other three final byte arrays before the first generated
   configuration file is created or written. B is not regenerated after the
   digest is derived.

`H.host_id` is the hostname domain, distinct from the machine fingerprint.
The trusted profile, Host and finite schemas receive no provider reverse hash.
`config_rx` remains the approved directory with null content hash. The digest
changes with actual root identity or trusted bytes; it does not use projected
identities. A parsed profile or `ResolvedConfiguration` remains diagnostic data
and cannot become an original installation owner or native issuer.

## Refusal, expiry and cleanup

Decidable schema errors occur during original plan parsing before any SDK
acquisition/effect. Late original pin/SD/source/normalizer failures retain the
original nodes and owner as UNKNOWN, with no partial generated configuration
write and no automatic retry. Existing original-owner, LABEL, alias binding,
known-success acknowledgement and compare-and-restore rules remain in force.

The compiler never renews expiry or changes project/ref. It retains the
existing canonical/one-year upper bound; it adds no current-expiry condition
to raw plan reparsing or cleanup. The unchanged `HostScopedProvider.verify_scope`
checks normalized scope, ref, project, pinned configuration and actual expiry;
runtime Stop/project Authority enforces permissions separately. Expired
configuration remains eligible for original comparison/cleanup while its
authorization fails.

## Software evidence boundaries

The dedicated test uses synthetic SDK exports and the existing original
Bootstrap/four normal loaders. Its extension to the stdio seam translates only
device/inode values into the same synthetic FileInfo domain; real disposable
fd bytes, times, sizes, modes and identity drift checks remain exercised. No
source attestation, owner, loader result or verifier is replaced. Nonempty
grants are software fixtures, not installed authority.

The new private run saves exact base/overlay inputs, actual import execution
bytes and code objects (SourceFileLoader and pytest rewrite), global-thread
recorder controls, full before/after maps, JUnit and selected/deselected node
rosters. This binds module execution bytes; it is not proof of all Python call
paths or native behavior. Old 753 evidence and its metadata limitations are
preserved without retrospective relabeling.

The frozen R5 baseline also contains an obsolete `provider_action` negative in
`tests/test_windows_scm_config_materialization.py`: it expects literal
`session.stop` to be unsupported, although the same published baseline's
provider supports it. That baseline/R5 failure and its exact test bytes are
retained separately. Main's narrow maintenance decision changes only that
mutation to `unsupported.action`, asserts its absence from published actions,
and keeps the same node and pre-SDK refusal assertions. The existing recipe /3
expired literal Stop positive is reused. R6 is a separate run; it does not
relabel R5 or add a current-expiry rejection. Current results and exact
paths/hashes are recorded in the dedicated compact evidence.
