# W26 release tooling

`tools/full_project_release.py` adds build and bundle checks around the existing
W22 wheel builder and `verify_wheel_install.py`. It does not start COMSOL and it
cannot certify a native capability. Its current wheelhouse target table uses
CPython 3.12 as the packaging baseline for this work session; the final release
matrix must record the Python build actually installed on each target.

## Product artifacts and source recovery

The `build-wheel` command stages only `comsol_mcp/**`, `pyproject.toml`,
`README.md`, and `LICENSE`. It ignores `.git`, `_state`, workpack evidence,
private models, vendor documents, COMSOL JARs, AppleDouble entries, and
other project data. The resulting universal Python wheel is the runnable
application layer; it is not a complete source recovery or scientific handoff.

For a full source recovery archive, create a reviewed JSON file with a nonempty
`files` array. Each element must be a project-relative file path, optionally
with `reason`, `distribution_class` (`LOCAL_RECOVERY_ONLY` or
`PUBLIC_SOURCE_CANDIDATE`), and `public_source_review_flags`. Paths are exact
files, not directory globs. The tool rejects symlinks, traversal, `.git`,
private state, `._*`, `.mph`, JARs, credential files, and prohibited vendor
document paths. The archive embeds a relative-path SHA-256 manifest, preserves
the file-level distribution labels, records that the archive is for local
recovery with publication unauthorized, and explicitly records that Git
history is not included. The current candidate list and exclusions are in
`source_recovery_plan.json`; its scope has parent approval for local archive
verification, but it is not the final post-W23–W25 source freeze. Regenerate
source hashes and the list after the integrated tree is frozen.

Example inclusion manifest:

```json
{
  "schema": "comsol-mcp-full-release-source-input/1",
  "files": [
    {"path": "pyproject.toml", "reason": "runtime dependency contract"},
    {"path": "uv.lock", "reason": "complete source dependency lock"},
    {"path": "comsol_mcp/mcp_server.py", "reason": "runtime entry point"}
  ]
}
```

## Wheelhouse flow

Export the existing lock without changing `uv.lock`. Set a private temporary
cache path because the workspace is on exFAT and a reused cache can contain
filesystem metadata that is unreadable here:

```sh
UV_CACHE_DIR=/private/tmp/comsol-release-uv-cache \
  uv export --locked --no-dev --no-emit-project --no-annotate --no-header \
  --python /path/to/python3.12 --format requirements.txt \
  --output-file /private/tmp/comsol-release-dependencies.txt
```

`compose-lock` adds the built application wheel and its measured SHA-256 to the
exported dependency lock. `download-wheelhouse` requests only binary wheels for
the selected OS/architecture and CPython 3.12 from PyPI, with hashes required.
It fails if the lock cannot be satisfied by a target-tagged wheel. Do not rename
a wheel to change its target. The app wheel remains `py3-none-any`; each
wheelhouse is target-specific because the contained binary dependencies carry
their own tags.

The Intel Mac target is macOS 12 or later, matching the COMSOL 6.3/6.4 Intel Mac
system requirements. The downloader requests `macosx_12_0` compatibility tags,
which include valid earlier deployment tags such as `macosx_10_15`; it rejects
wheel tags that require a later OS than the target minimum. The wheel filenames
and internal tags remain unchanged. This checks artifact compatibility only,
not COMSOL execution.

The source snapshot reads the allowlisted source twice, records each file size
and SHA-256, and aborts if its inventory or bytes change while copying. The
wheel-build receipt binds the universal application wheel SHA-256 to that
source manifest. `package-bundle` requires both receipts and checks that the
wheelhouse contains the exact application wheel from that build.

`manifest` accepts a flat wheelhouse and a complete hash-pinned requirements
file. It checks that every active target requirement has one wheel with the
locked version and SHA-256, rejects unlisted files, checks wheel tags and scans
all compressed members. It writes `PACKAGE_MANIFEST.json`, a CycloneDX 1.5
`SBOM.cdx.json`, and a license inventory including in-wheel license-file hashes.
Unknown package license metadata remains `UNKNOWN`; the report does not infer a
license from the package name.

Archive scanning rejects all JAR and class files except the `org.jpype.jar`
bootstrap contained within the official `JPype1` wheel; its license and notice
are included in that wheel's license inventory. The exception is limited to
that exact dependency path and does not permit COMSOL or other vendor binaries.
The Windows `pywin32==312` wheel also carries the public top-level
`PyWin32.chm` help file. Only that exact member and wheel tag are allowed; the
package manifest records its size and SHA-256 alongside the wheel hash. Other
CHM/vendor documents remain rejected.

To make a self-contained installation archive, run `package-bundle` after the
wheelhouse manifest is written. The archive carries the target wheels, `uv.lock`,
the complete pip requirements lock, release helper, package manifest, SBOM,
license inventory, operations guide, and the application source/build receipts.
An embedded member manifest hashes every file except itself, including the
offline instructions. The tool checks all source locks and build receipts
immediately before packaging.

This repository is on exFAT, where native extended attributes can appear as
AppleDouble `._*` files. Build wheelhouses and bundle archives in APFS staging
such as `/private/tmp`; `scan` rejects AppleDouble members. Copy only the
validated single archive back to the SSD, then compare its SHA-256 and inspect
its member list. Do not copy an unpacked tree and assume POSIX permissions or
symlink semantics survived.

## Clean install and doctor

`install` and `offline-install` create a new venv and refuse an existing target.
They install with `--no-index`, `--only-binary`, and `--require-hashes`, then run
`pip check`, resolve the installed `comsol-mcp` distribution and console entry
point, import the packaged resources, and verify the import came from the new
venv. `doctor` performs those local checks only. It reports engine and feature
capabilities as `NOT_PROBED`/`UNVERIFIED`; importing an API is not a successful
COMSOL execution. Successful and incomplete helper-created venvs carry a
`.comsol-mcp-install.json` marker bound to their canonical root and this
project's install identity. The marker also stores a content inventory after
the lock install. Generated Python bytecode caches are ignored; additions,
deletions, or content changes elsewhere, plus any COMSOL model-like artifact,
block cleanup. Existing environments without a usable marker and baseline are
not eligible for this cleanup command.

The read-only Windows inventory recorded on 2026-09-27 returned 320 process
rows; 19 had both executable path and command line unavailable. Only the exact
kernel identities `(PID 0, System Idle Process)` and `(PID 4, System)` are
provable exceptions. The other 17 rows, including protected services and
`svchost.exe` instances, remain ambiguous and make the Windows uninstall guard
return `UNKNOWN`. This host snapshot therefore verifies fail-closed behavior,
not successful Windows uninstall; do not exempt those rows by process name.
The summary is preserved in
`docs/full_project_execution/evidence/luna_w26_release_foundation/windows_process_summary_root_authorized.json`.

On macOS, `offline-install` launches the install tree under an individual
`sandbox-exec` profile denying `network*`. Before pip runs, a child process tries
to connect to a one-use loopback listener owned by the supervising process; the
test passes only when the OS sandbox denies that connection and the listener
receives nothing. This proves process-level network denial for that test tree.
It does not establish the separate T056 LAN-host case, run a COMSOL model, or
certify any Windows/Intel machine. When `sandbox-exec` is absent or the probe is
inconclusive, the command fails closed.

`transition-check` installs a prior complete hash lock, records the application
artifact and full installed-distribution inventory, attempts the candidate
hash lock, and always attempts the old-lock rollback after a candidate install
was invoked, including when pip reports failure. It then removes candidate-only
distributions from that disposable venv and verifies the original artifact,
application version, and complete distribution inventory. A failed candidate
or rollback remains a failure even if cleanup later restores the files.
Candidate-only cleanup records attempted names separately from names absent
from the verified post-cleanup inventory; a missing cleanup exit status remains
`UNKNOWN` even if the inventory later appears restored.
Both lock files are copied byte-for-byte into the run evidence directory and
their source paths, sizes, and hashes are recorded before the install starts.
`version_migration_verified` is always `false`: this test does not open or
migrate application data or schemas. Never use this disposable check as an
instruction to mutate a user's active environment.

`uninstall` removes only one marked isolated venv. Without `--confirm` it writes
an exact-root plan and leaves the target untouched. With `--confirm`, it
requires a successful process inventory both before and immediately before
removal, refuses a busy or unknown process state, rejects malformed or
misbound markers, protected roots/source paths, content drift, and nested
mounts, and records plan/result JSON in a new evidence directory outside the
venv. The deletion scope is the exact marked venv tree; it does not traverse
symlink targets or mount points. Any file not present in the recorded install
inventory blocks removal, so models, project files, or user data placed in the
venv are preserved by refusal. This is not a general Python package-manager
uninstall and will not remove unmarked or shared environments.

## Acceptance claim ceiling

This tool contributes release evidence to the existing contract and does not
replace the original tests:

| Original case | What this tool can show | Still unverified here |
|---|---|---|
| T034 | no fixed install prefix in the install commands; exact file hashes | cross-OS migration and native rebinding |
| T035 | no symlink, AppleDouble, vendor JAR, MPH, credential file, or nested archive member in inspected package inputs | product API authorization and user-account ACL behavior |
| T050 | complete Python lock and artifact restore in a disposable install | COMSOL model snapshot/save counts and scientific restore |
| T056 | target-tagged wheels; no-index hash-checked install; process tree blocked from network | target machine, allowed LAN host, complete native small-model execution |
| T059 | doctor reports only installed Python imports and labels engine capabilities `UNVERIFIED` | any API capability becoming `VERIFIED` after a real target operation |
| T060 | package members, lock, wheels, license metadata, SBOM, and hashes are linked | W23/W24 source recipes, MPH/complex-field data, images, numerical output, and native replay |

T036–T043 behavior outside those artifact checks remains with the full product
implementation and integrated acceptance map. None of these tool outcomes
can promote a Windows/6.3 or other matrix cell to PASS.

## Commands and state boundaries

```sh
python tools/full_project_release.py scan release/wheelhouse --output release/scan.json
python tools/full_project_release.py build-wheel --out /private/tmp/comsol-release/application_wheel \
  --python /path/to/build-python --evidence docs/full_project_execution/release/candidate/build
python tools/full_project_release.py compose-lock --dependencies /private/tmp/comsol-release-dependencies.txt \
  --application-wheel /private/tmp/comsol-release/application_wheel/comsol_mcp-0.1.9-py3-none-any.whl \
  --target macos_arm64 --out /private/tmp/comsol-release/macos_arm64.requirements.txt
python tools/full_project_release.py download-wheelhouse --python /path/to/python3.12 \
  --target macos_arm64 --requirements /private/tmp/comsol-release/macos_arm64.requirements.txt \
  --application-wheel /private/tmp/comsol-release/application_wheel/comsol_mcp-0.1.9-py3-none-any.whl \
  --out /private/tmp/comsol-release/macos_arm64/wheelhouse \
  --evidence docs/full_project_execution/release/candidate/evidence \
  --source-lock uv.lock
python tools/full_project_release.py package-bundle \
  --wheelhouse /private/tmp/comsol-release/macos_arm64/wheelhouse \
  --requirements /private/tmp/comsol-release/macos_arm64.requirements.txt --source-lock uv.lock \
  --metadata docs/full_project_execution/release/candidate/evidence/macos_arm64 \
  --build-evidence docs/full_project_execution/release/candidate/build \
  --operations-doc docs/full_project_execution/release/RELEASE_OPERATIONS.md \
  --target macos_arm64 --out /private/tmp/comsol-release/comsol-mcp-macos_arm64.zip \
  --evidence docs/full_project_execution/release/candidate/evidence
python tools/full_project_release.py offline-install --python /path/to/python3.12 \
  --requirements /path/to/extracted/locks/requirements.lock \
  --wheelhouse /path/to/extracted/wheelhouse \
  --venv /private/tmp/comsol-release-fresh-venv \
  --evidence docs/full_project_execution/release/candidate/evidence
python tools/full_project_release.py uninstall --venv /private/tmp/comsol-release-fresh-venv \
  --evidence /private/tmp/comsol-release-uninstall-evidence
python tools/full_project_release.py uninstall --venv /private/tmp/comsol-release-fresh-venv \
  --evidence /private/tmp/comsol-release-uninstall-confirmed --confirm
```

The final integrated build must use the post-W23–W25 source candidate and its
final dependency locks. Earlier build/install evidence is a tool exercise, not
the formal final release. Six OS/COMSOL combinations, three architecture
artifacts, engine/module licenses, complete model runs, desktop/UI access, and
independent review stay separately evidenced.
