# Workpack tooling only

bootstrap.py: fixed commit/tree/blob recovery, no candidate overlay, no dependency install or COMSOL.
init_resume.py: verifies original requirements and scope decision, indexes existing state plus latest checkpoint; never zeroes upstream matrices.
candidate_inventory.py: hashes selected source files across root/candidate trees; lists actual byte differences and declared conflicts. No automatic merge/approval, no opening of mapped private source paths.
status.py/checkpoint.py: compact current continuation pointer and safe state update; not native job observation.
preclean_inventory.py: Git path status only; no content backup/deletion or clearance to delete.
verify_package.py: package hash integrity, not product correctness.

All tools use standard-library Python; Git is optional for archive recovery. Target-machine recovery and product tests must still actually run. Local fixture tests do not certify Windows, PowerShell, COMSOL, reviewer execution or network downloads.
