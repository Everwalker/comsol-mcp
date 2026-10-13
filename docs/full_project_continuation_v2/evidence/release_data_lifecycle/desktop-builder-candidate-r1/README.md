# Darwin desktop and CPython 3.12 builder candidate

This is a review-only acquisition candidate. It does not change `pyproject.toml`, `uv.lock`, or project version 0.1.9. It does not authorize downloads, installation, or builds. The candidate is not a formal lockfile.

`candidate-plan-r1.json` binds the existing base/dev plan and source inputs by SHA-256, records exact wheel/sdist candidates, exposes per-target closures, and lists a globally deduplicated URL/SHA set. `metadata-pointers.json` points to the original PyPI release JSON and first-party source metadata; metadata payloads and artifact binaries were not saved locally. The OpenSSL exact byte count is cross-checked against two archive indexes because the first-party release page reports a rounded size only.

The unchanged base/dev plan has 50 unique artifacts totaling 69,625,484 bytes. The additional Darwin Desktop closure has seven unique wheels totaling 7,596,242 bytes across ARM64 and x86_64 (the five universal2 PyObjC wheels are shared, while psutil uses one ABI3 wheel per architecture). The added project-builder wheels are setuptools 83.0.0 and wheel 0.48.0, totaling 1,041,410 bytes; packaging 26.3 is already in the existing target plan and satisfies wheel's `packaging>=24.0` requirement.

For Intel cryptography, the locked 50.0.1 sdist (880,381 bytes) is already included in the base/dev plan but is not installable as the required target wheel. Its first-party source metadata requires maturin >=1.14.1,<2, cffi >=2.0.0, and setuptools. The candidate adds the exact Intel maturin 1.14.1 wheel (10,169,205 bytes) and OpenSSL 4.0.2 source archive (55,153,883 bytes); cffi and pycparser are reused from the existing Intel base/dev closure. The complete enumerated union is 61 unique URL/SHA pairs totaling **143,586,224 bytes**. Separate target staging totals repeat shared artifacts and are listed in the JSON.

The Intel native source build remains incomplete. This host has only an ARM64 CPython 3.12.13 runtime, no `rustc` or `cargo`, and no Cargo registry cache/source tree. Cryptography 50.0.1 uses a locked Cargo dependency graph, so the Cargo crate source closure and an x86_64 CP312 build host still need exact resolution. The byte total above explicitly excludes those unresolved inputs and is not a full offline-build size.

Run the offline plan check with:

```sh
python3 candidate_plan_tool.py --validate
```

Render, but do not execute, pinned fetch commands with:

```sh
python3 candidate_plan_tool.py --dry-run --cache-root /PATH/TO/NEW-TASK-CACHE
```

After a separately authorized acquisition, the same tool can verify an existing cache by exact filename, regular-file identity, size, and SHA-256:

```sh
python3 candidate_plan_tool.py --verify-cache /PATH/TO/NEW-TASK-CACHE
```

The tool has no network client and no installer/build mode. A `PASS_CANDIDATE_PLAN_OFFLINE_VALIDATION` means only that this candidate and its local source bindings/totals are internally consistent.
