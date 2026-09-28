# Bounded offline test execution

Use `tools/run_bounded_tests.py` for an explicitly frozen offline test selection. This is engineering evidence, not native COMSOL or scientific acceptance. The checked-in `tools/test_efficiency_baseline.json` is one macOS host snapshot; it is not a portable environment or a platform-support claim.

## Workflow

1. Main defines one bounded implementation unit, relevant test modules and acceptance. The same Luna Max executor owns implementation, focused tests and repair. Main reviews the final diff and material counterexamples; do not duplicate every debug step.
2. Fix the base commit and candidate source closure. Include the runner, selected tests, their tool dependencies, and tracked production Python/JSON dependencies. Declare the exact base-to-candidate source overlay, including the baseline file; the baseline itself is identified separately to avoid a self-hash cycle. Do not mix unrelated source changes.
3. Explicitly freeze the manifest before running. Changing the candidate requires a new deliberate freeze; the runner never silently updates hashes. Preserve failed receipts.
4. Run the selected focus plus required regression. Read the compact stdout first. Open raw logs/JUnit only for a failure or review need. Do not rerun unchanged suites without a new failure, change, or acceptance requirement.
5. Verify actual process exit, JUnit result/counts, unchanged candidate identity and required stages. Historical evidence can be referenced by its original fingerprint/time, but cannot be relabeled as a fresh run.
6. Main reviews the cohesive stage, updates root PROGRESS.md and existing state, and synchronizes ordinary Git history. Final independent project review remains mandatory.

## Current host replay

The APFS volume is backed by an image on the external SSD; its mountpoint is not internal-disk storage. If absent, inspect mounted-image identities before attaching the existing image. Never format or detach another disk. The observed successful command was:

```sh
hdiutil attach -nobrowse /Volumes/SSD/Comsol-MCP/execution-scratch/test-efficiency/te-apfs-20260928-01.sparseimage
```

Verify the actual mountpoint and update a new stage manifest explicitly if it differs. The existing shared Python runtime remains at its recorded historical path pending process cleanup; do not relocate or delete an occupied venv. All new test temporary output is directed to the external APFS image.

From the repository, the reviewed invocation is:

```sh
python3 -B tools/run_bounded_tests.py --baseline tools/test_efficiency_baseline.json --with-required-regression
```

The interpreter running selected tests is pinned in the manifest. The launcher uses Python standard-library orchestration. Runtime mount/write permissions may require normal tool escalation. Raw files remain under the image and compact receipts under external `execution-scratch/test-efficiency`; they are not committed.

## Explicit next-stage freeze

Review and edit a copied baseline: candidate ID, ancestor base commit, complete source closure, overlay, test nodes/counts, fixture identity and environment. Then calculate its declared source hashes once:

```python
import hashlib, json
from pathlib import Path
p = Path("tools/test_efficiency_baseline.json")  # choose the reviewed stage manifest
d = json.loads(p.read_text())
d["source_sha256"] = {s: hashlib.sha256(Path(s).read_bytes()).hexdigest()
                      for s in d["source_paths"]}
p.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")
```

Hashing is not acceptance. Wrong interpreter/imports, missing fixture, candidate drift, incompatible scratch, nonzero exit, missing/malformed/contradictory JUnit, zero execution or omitted required checks must not be promoted to a complete PASS. The tool does not authenticate arbitrary test code as safe to launch native engines; only use explicitly reviewed offline selections.

## Limits

The APFS preflight is currently macOS-specific. Other host adapters, native runs, optional extended-attribute probing and optional Java compilation are not established by this checkpoint. No quota-saving percentage has been measured. The intended reduction is fewer model round-trips, smaller returned logs, and fewer environmental reruns, without reducing acceptance coverage.
