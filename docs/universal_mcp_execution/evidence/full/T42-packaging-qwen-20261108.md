# T42 packaging preparation — two temporary bundles and Intel dependency gap — 2026-11-08

Executor: an independent task sub-agent ran on local model `omlx/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S` at max reasoning; the GPT main agent coordinates integration. The original wording incorrectly described this sub-agent as the main chat.
Task scope: T42 (U10/U12) preparation only. Per user instruction, this run did NOT freeze or mark final three-platform artifacts because T14/T19/T20 are still changing production source. Per parallel-coordination instruction, README was not touched.

## Candidate identity (pinned, committed)

- Git commit: `dab6dfd8873d67870de5163590cf5b5e31aebac8` (HEAD at snapshot time; still HEAD at report time).
- Snapshot route: per-leaf `git ls-tree` + `git cat-file blob` (git archive could not be used: sparse-restore promisor lacks some intermediate tree objects locally and network fetch is not permitted). All 205 leaf blobs verified present.
- Snapshot allowlist: `comsol_mcp/** pyproject.toml README.md LICENSE` → 205 files, snapshot manifest sha256 `208cf67b619b2c2fe217afef7ef739da4d78de15102f6f799f9fdef88b95e473` (`_state/t42-offline-package-20261108/fixed-source-manifest.json`).
- Uncommitted working-tree edits in `comsol_mcp/` (other agents' T14/T19 work) are NOT in any artifact here.

## Private scratch

All writes under new untracked directory: `_state/t42-offline-package-20261108/` (plus this evidence file).
Nothing else was modified: no production code, no repo scripts, no tests, no G*.json, no FULL_COMPLETION.json, no README. `git status` shows only `?? _state/t42-offline-package-20261108/` plus this new evidence file from this task.

## Reusable cache inventory and verification (read-only)

1. Current committed locks (repo HEAD): `uv.lock` sha256 `d3dc467949c87be983201fa8440772b99e3aec45c4a0e4e2631b2a024b9ec1fe` (916 sha256 records; per-file hashes present). Secondary non-hashed pins: `requirements-windows-cp312.txt`, `constraints-macos-arm64-py313.txt`, `constraints-macos-arm64-py314.txt`.
   - Drift recorded: `requirements-windows-cp312.txt` pins `uvicorn==0.54.0` while `uv.lock` (bundle lineage) pins `uvicorn==0.53.0`. uvicorn 0.54.0 wheels are NOT cached anywhere locally. T43 must pick one lineage explicitly; the T42 bundles follow the tool/uv.lock lineage (0.53.0).
2. Three-target wheelhouse cache: the recorded 2026-10-25 CURRENT9BD build tree (`_state/universal-current9bd-source-package-20261025-b63eb8d66f68/release-r1/{win_amd64,macos_arm64,macos_x86_64}/wheelhouse`, per `evidence/CURRENT9BD_OFFLINE_BUILD_MAC_INSTALL_SCOPE.json`) was found **deleted** — it now resides in volume trash `/Volumes/SSD/.Trashes/501/COMSOL_MCP_UNIVERSAL_WORKPACK/_state/...` (120 wheels incl. app 0.1.10 + 3 builder wheels). Trashed bytes were read only, never modified or moved.
3. uv.lock delta audit: old (a6bb3dc0) vs current (d3dc4679) lock pins differ only in `comsol-mcp` (0.1.10→0.1.16). Every dependency pin is unchanged, so cached wheel bytes are in principle reusable for the current lock — verified per-file, not assumed.
4. Interpreter/build conditions found locally: CPython 3.12.13 (/opt/homebrew/opt/python@3.12) is the tool-defined install interpreter for all three TARGETS; builder bootstrapped offline from cached builder wheels (setuptools 80.9.0 sha256 062d3422…, wheel 0.48.0 sha256 3217dcc8…, packaging 26.3 sha256 d7193f7c…, copied from the trashed `universal-release-tooling/builder-wheels`). Bootstrap used `--no-index --no-deps --find-links` without `--require-hashes` (hashes recorded above for the record); the final freeze should pin the bootstrap lock too.

## Artifacts built from the pinned commit (all offline)

Tool: repo `tools/full_project_release.py` (unmodified). Export: `uv export --frozen --no-emit-project --no-dev --extra desktop --offline` → sha256 `36291967232d129a1990fae5d1153b6048c18d23745b957026407af36de786ff`.

- Application wheel (single, same-source for all three architectures):
  `comsol_mcp-0.1.16-py3-none-any.whl`, 3,208,896 bytes, sha256 `0476a5d78f1543105a67b1e947f5e44cc3109dee45fafeb7738c9ac5670dd001`, status WHEEL_BUILT_NATIVE_UNVERIFIED (build evidence: `_state/t42-offline-package-20261108/build-evidence/`).
- Composed install locks (`compose-lock`, identical file for all targets by current tool design; pip filters markers at install time): sha256 `013ec1bb9e7229588a341a48968fa14db533107bd76564db118f1a2f586afaca` (76,887 B). Per-target tool-filtered download locks: win `1c018077…`, arm `146cc8ed…`, intel `6ed4bbe1…`.
- Staged wheelhouses (copies from the verified cache + new app wheel; every staged wheel's sha256 verified in-script against the composed lock via the tool's own `parse_hash_requirements`):
  - win_amd64: 35/35 pins matched and hash-verified; `staged-summary.json` missing=[].
  - macos_arm64: 42/42 hash-verified.
  - macos_x86_64: 41/42; only `cryptography==50.0.1` unmatched — see gap below.
- Manifests (hash/SBOM/LICENSES audit bound to current uv.lock d3dc4679): PASS for win/amd (35 wheels) and macos_arm64 (42 wheels) — `{win_amd64,macos_arm64}/metadata/`.
- Offline bundles (package-bundle, with operations doc marking TEMPORARY status):
  - `win_amd64/offline.zip` 31,477,459 B sha256 `3cb5a76ee21e07e980e07ca31fd88933b6a77da46dc88bf38ab9d2b13e80ef01`
  - `macos_arm64/offline.zip` 31,421,735 B sha256 `5401806d6adea9a714a4e4b9c9efa5424ea3df85de1d709ff0bb16549a892217`
  - No intel bundle (incomplete by design; see below).

## Offline / no-implicit-download preflight results

Mechanism enforced everywhere: `pip --no-index --only-binary=:all: --require-hashes --find-links wheelhouse` (plus this session's own network-isolated sandbox as a second layer).

- macos_arm64 (native host, CPython 3.12.13 venv at `/private/tmp/t42-offline-package-20261108/macos_arm64-venv`): tool `install` → status PASS. pip install exit 0; offline resolution 42/42 with hashes; doctor PASS (installed_under_prefix true, version 0.1.16, entry point exists, 202 package files, 9 java sources, 1 action catalog, 1 schema); `pip check` exit 0. venv left in place for T44 follow-up work.
- win_amd64 (static cross-check, no Windows machine): `pip download --no-index --require-hashes --platform win_amd64 --python-version 3.12 --implementation cp --abi cp312/abi3/none` with the tool's target-filtered lock → exit 0, all 35 wheels resolved and hash-verified offline.
- macos_x86_64 (static cross-check): same form → exit 1, exactly one missing distribution: `cryptography==50.0.1` (uv.lock publishes no macosx x86_64 cryptography wheel; upstream dropped them — the project uses a locally derived wheel instead).
- Old-intel cache integrity replay: with the 2026-10-25 `requirements-derived.lock` against the trashed original intel wheelhouse, offline pip resolved 41/41 with hashes (exit 0). The cached derived cryptography wheel bytes equal the 2026-10-26 provenance exactly: 4,043,680 B sha256 `2973023be3599d26…`; OpenSSL license LICENSE.txt 10,175 B sha256 `7d5450cb2d142651…` matches the provenance record.
- Process-level offline isolation (tool `offline-install` via `sandbox-exec -f network-deny.sb` with loopback listener probe): NOT reproducible inside this agent session — spawning sandbox-exec returns EPERM here. This is a session-sandbox limitation, not a tool defect; the prior scoped run `CURRENT9BD_OFFLINE_BUILD_MAC_INSTALL_SCOPE.json` retains the accepted process-level evidence, and this session's claim is limited to the pip-flag level.

## Identified real gaps and exact unblock conditions (nothing was worked around)

1. macos_x86_64 bundle incomplete for the current lock: `cryptography 50.0.1` has no upstream macosx x86_64 wheel. The locally derived wheel bytes are cached and byte-verified (above), but both on-disk provenances (2026-10-25/26) are bound to the OLD uv.lock sha `a6bb3dc0…`; `verify_derived_wheel` requires binding to the selected uv.lock (`d3dc4679…` now). Unblocks at final freeze with zero network: re-run `compose-derived-lock`/`manifest`/`package-bundle` using a provenance re-attested against the final uv.lock (re-run native audit fields over the same cached wheel; inputs are all local). No Intel hardware is needed for the distribution build (native remains USER_REQUESTED_SKIP_HARDWARE; architecture build remains required per TARGETS).
2. Windows/mac native execution is out of this task: wheelhouse bytes and locks are complete for win/arm, but installed+exercised acceptance belongs to T43 (Windows x64 6.3/6.4) and T44 lifecycle rows. No artifact byte here is claimed as machine acceptance.
3. requirements-windows-cp312.txt vs uv.lock uvicorn drift (0.54.0 vs 0.53.0): recorded, not fixed; 0.54.0 wheels are not cached, so honoring that lineage offline needs one authorized download of `uvicorn-0.54.0-py3-none-any.whl` (1 file) or a lock edit decision by the T41/T43 owner.
4. Builder bootstrap (setuptools/wheel/packaging) reused from trashed cached wheels without hash-pinned bootstrap: hashes recorded; final freeze should bootstrap from a require-hashes lock.
5. Interpreter condition: all three TARGETS in the tool are CPython 3.12. The repo's `constraints-macos-arm64-py313/314.txt` describe other (py313/py314, unhashed, pip-freeze-style) environments; if T43/T44 target interpreters move to 3.13/3.14, the whole 3.12 wheelhouse must be regenerated per target (bytes for cp313 exist only partially in local uv caches — not verified for this task).

## Temporary-candidate caveats (as instructed)

- These artifacts are frozen to commit `dab6dfd`; T14/T19/T20 continue on source. At the final candidate freeze the application wheel, export, composed lock, manifests and bundles MUST be rebuilt from the frozen commit (dependency wheel bytes themselves are expected to be reusable byte-for-byte unless uv.lock pins change).
- T42 status is intentionally not written into FULL_COMPLETION.json (owned by the integrator); G8.json untouched. This file is the independent evidence for the integrator to integrate.

## Reproduction pointers

All command stdout/stderr under `_state/t42-offline-package-20261108/logs/` (uv-export, pipcheck-win/win2/arm/intel/intel-old, install-arm, package-bundle-*); staging matrix in `staging-summary.json`; bundle receipts in `{win_amd64,macos_arm64}/evidence/offline-bundle-*.json`; doctor/install in `logs/install-arm.json`.
Trashed cache roots read (never written): `/Volumes/SSD/.Trashes/501/COMSOL_MCP_UNIVERSAL_WORKPACK/_state/universal-current9bd-source-package-20261025-b63eb8d66f68/` and `.../repository/_state/universal-normal-pip-coherent-release-20261026-0e8f8b2c609243489e86cd7c769e01cf/` and `.../repository/_state/universal-release-tooling/builder-wheels/`. Historical Windows Stage2 UNKNOWN/Worker/SSH was not queried or touched; no COMSOL/native was started; no network or escalation was used.

