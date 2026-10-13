# Continuation checkpoint — source, not final acceptance

The tree root is the complete current 0.1.9 production checkout. It is not a new 0.1.10 integrated release. Independent candidate source is preserved under `candidates/`; never overlay these trees automatically. See INTEGRATION_CONFLICTS.json for byte differences needing integration review.

- Windows R19: complete candidate source and software evidence; native Windows acceptance remains separate.
- W23 R14: full candidate and final6 packet, CHANGES_REQUIRED (shared-measure regression and approval021/metadata022 mismatch). Activity022 NOT_RUN. R15 is preserved separately under candidates/w23-r15 with 426 exact frozen source members; Root and Independent scoped source/software/fixed-entry PASS; Activity022 NOT_RUN. Its fixed metadata and shell entry are included.
- W24 coupled05: independent candidate, no new scientific execution in this checkpoint.
- W26 offline dependency-venv unit R1: Root and Independent scoped source/software PASS; all eight target venv/pip/profile commands NOT_RUN. Packaging is 26.3 from the actual lock. Current runtime observation mismatch remains UNFIXED.
- Successful dependency runtime evidence is limited to ARM TLS/hashlib/lzma/tar.xz and actual fat bytes; Intel hardware and complete three-architecture delivery remain OPEN. gdbm/Tk failures are preserved.

## Source reconstruction on another machine

1. Use the root checkout for ordinary package/test/build entrypoints; Java source is included, commercial COMSOL/JAR binaries are not.
2. Choose a single candidate and compare its full source against the root; resolve listed conflicts explicitly. Candidate native launchers contain immutable historical local paths and guards. Do not run them unchanged or replay an old once invocation.
3. `EXTERNAL_SOURCE_MAP.json` maps every additionally available frozen source to included exact bytes in external-source/. Frozen originals are unchanged. Administrative wrappers and their imported helpers are included; reconstruct fresh private paths using these sources, then regenerate context/freezes and obtain a new source/once review. Never reuse the published approval hashes as authority for a fresh native action.
4. Reacquire fixed official inputs using included exact acquisition manifests, checksums/signature fingerprints and offline locks. LOCAL_ONLY_MANIFEST_R2.json identifies original large inputs/artifacts by SHA/size; these are not embedded. Actual native checkpoint inventories are historical evidence, not substitutes for a new machine's owner/runtime identity.
5. MISSING_SOURCE_REFERENCES.json lists old deleted temporary paths and some historical command strings. They are not claimed recoverable. Current production source and current candidates are present in full; historical native proof remains local when indexed only.

New large MPH, commercial libraries, wheels, runtime trees, caches, raw private ledgers and broad logs remain local. Already-public origin/main artifacts are retained unchanged; this checkpoint performs no unrelated deletion or historical rewrite. Compact failure/UNKNOWN receipts and source hashes are retained. No source patch erases the original failures. No COMSOL/JVM/native test was started while preparing this publication.

The main Agent supplies the final canonical PROGRESS/NEXT/RESUME pages and approved publish commit. This preparation did not push to GitHub or modify the original .git.

## Current round stop boundary

See checkpoint/CURRENT_STATE.json and STATES.json. After ordinary verified main synchronization, pause; do not dispatch Activity022, W26 dependency environments or any new scientific/native phase automatically. Runtime identity mismatch remains UNFIXED/UNVERIFIED; full W01–W26 completion and unified final acceptance remain OPEN. All three progress histories retain their original tails with the same current header.
