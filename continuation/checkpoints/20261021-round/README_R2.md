# Portable source checkpoint R2

This is a byte-exact source/static/test handoff, not a product release, native acceptance, approval, or authorization to run an old entrypoint. The public baseline is `65ced71e9d63c56e6b6009984ae5c4185f4be3bd`.

The package includes all reviewed source/static rows for W23/W25 (1,145), W24 R20 (900), W26 R31 (905), and W26 R32 R3 (908). It also includes the current Windows R6I R2 native-entry payload/wrapper/test, R2 metadata-only probe/wrapper/test, and bound-PREP wrapper/test: 9/9 required exact source rows, with 8 newly added from the latest coverage finding. Current R6I packet manifests, unsigned plans, source maps, diffs, and non-authority templates are included. Historical snapshots remain separately labeled; no merge is performed.

`SOURCE_MAP_R2.json` describes restore roots. Restore only to a new directory with `python3 restore_checkpoint_sources.py --output /path/to/new-directory`. It verifies each content-addressed object and restored file hash, writes a readback report, sets all files to mode 0644, and never executes source.

No commercial COMSOL/JDK libraries, `.mph`, database, virtual environment, installed packages, compiled classes/bytecode, wheels, runtime databases, credentials/tokens, effective approvals, once records, PIDs, or active execution results are restored. The metadata-only R2 result is an instantaneous metadata observation; it does not prove terminal status of the older native activity. Frozen Windows source bytes can contain historical account/SID/path literals; the source is included, while execution identity, Windows permissions, SSH target, and original inode bindings remain LOCAL_ONLY and require a fresh host-specific review/bind before use. Unsigned templates are not authority.

Three TLS files are exact public-base test fixtures, not user credentials. W26 source reviews and software controls are scoped evidence only. Native science acceptance, Windows R6I native execution, W25 GUI acceptance, final independent review, and release remain NOT_RUN or separately pending. The source pack does not upgrade those states.
