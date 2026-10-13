# Exact Intel input execution candidate R2

Status: source prepared; acquisition, unpack, configuration, installation, build,
and Intel runtime acceptance have not run.

The acquisition manifest fingerprint is
`28f53a6f03cc57383a433064bd2fb4459d301bcce46f846962e1af342a6e9c42`.
Root verified its 43 rows and 220,243,712-byte aggregate in the sibling
`w25-native-delivery-preparation-r2/ROOT_INTEL_METADATA_AND_ACQUISITION_REVIEW_R3.json`.
The current downloader/unpacker candidate is `SOURCE_FREEZE_R2.json`, fingerprint
`5715bbcbe145ee5af56a64d5041dd02e1cff454528cf0e162ae552326958adc6`.
The CPython candidate is `CPYTHON_STAGE_SOURCE_FREEZE_R2.json`, fingerprint
`9360a717d554da473be2fd142c93c7c7440d6729adba2e3cbb3928c61748dbda`.

Thirteen local adversarial checks passed after the R2 parent-directory guard
change. Their stdout/stderr exist in the tool transcript only; no standalone raw
log was captured. They prove the fixture boundary, not network or build success.
The changed R1 source preimages were reconstructed and checked against both
original frozen SHA-256 values in `r1_preimage/`; the old receipts remain intact.

After Root's narrow source review, execute the exact downloader with
`--execute-download --approved-manifest-sha256` set to the fingerprint above.
Before invoking it, verify every current source against `SOURCE_FREEZE_R2.json`.
Capture actual argv, stdout, stderr and exit code in a new verified private task
directory. Its output supplies the actual acquired prefix; do not substitute a
guessed prefix. The downloader creates one fresh private prefix and fails closed
on source/hash/size/redirect mismatch. Preserve the original partial and receipt.
Do not switch mirrors, modify manifest rows, or automatically retry with a new
source. Signed GitHub redirect URLs and response headers remain task-local.

On exact acquisition success, run `prepare_offline_builder.py --prefix` with the
observed canonical prefix. It checks all 43 opened archive hashes before creating
`sources/`. It rejects escaping paths, hardlinks, devices, duplicate entries and
escaping symlinks; creates symlinks after ordinary files; preserves failed trees.
No archive installer is invoked by preparation.

The explicit CPython stages are `configure`, `headers`, `build` and `install`,
each requiring `--execute-stage`. Use the actual SDK with
`--enable-universalsdk` and `--with-universal-archs=universal2`.
The header probe compiles and links each slice without running either probe.
Its success establishes compile/link evidence only. The actual Python library,
deployment floor, linked closure and final wheel require separate inspection.
No fake platform variables, target sysconfig files or ARM-only target headers
are admissible. Timeout preserves an owned process for identity reconciliation;
it never establishes termination or permits another duplicate build.

Rust component installation, exact Cargo vendor metadata, OpenSSL target build
and maturin final-wheel stages still require implementation against the acquired
inputs. No system Python, user Rust/Cargo state, live project lock or COMSOL
state is changed by this preparation. Intel hardware/runtime acceptance remains
NOT_RUN, even if an x86_64 wheel later cross-builds successfully.
