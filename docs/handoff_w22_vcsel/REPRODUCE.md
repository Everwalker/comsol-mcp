# W22 source and runtime reproduction

Scope: static non-axisymmetric synthetic VCSEL irradiance, transient 3D thermal response, finite distance/power search, and native deliverables. W21 scoped approval is inherited. W23 is excluded. No measured optical absorption, divergence or real-device thermal calibration is claimed.

Use `benchmark_spec.json` as the frozen execution contract. Its SHA-256 is `d94322296feff1cd7678e345bf798616a704c638c8c108d3c8f78f4128f4b1fe`. The geometry entry selects the 40 mm square slab; the historical `fixture.workpiece_radius_m` field is not the geometry used by this benchmark. The circular top ROI is radius15 mm. The independent reference is the full3D rectangular slab spectral solution, not a lumped model.

Build from this source using a separate Python environment containing setuptools/wheel. On exFAT, use the build helper so AppleDouble metadata is not packaged:

```sh
python tools/build_w22_wheel.py --out /path/to/new/wheel-directory
```

The helper stages only normal package source files on the interpreter's temporary filesystem and writes `build_receipt.json` with every included source hash. Install the resulting wheel through pip in a new Windows virtual environment; `pip check` must pass. Do not use editable installs, inject a service, copy commercial JARs, or apply the old W21 patch.

Copy the wheel, `tools/run_w22_native.py`, `tools/run_w21_native.py`, `tools/java/W22Fixture.java`, `tools/java/W22Outputs.java`, and `benchmark_spec.json` into one task directory. `run_w21_native.py` supplies the unchanged owned-instance launcher; all model actions use the installed public MCP stdio entry and ManagedBackend/Worker. Discover the installed COMSOL and external JDK11 paths. Runtime, private control, project and venv roots remain separate. The private launcher references read-only installed binaries; its loopback XML copy does not modify the installed XML.

Example PowerShell (substitute current paths; every `--work` must be NEW):

```powershell
& C:\task\venv\Scripts\python.exe C:\task\run_w22_native.py --work C:\task\full64 --version 6.4 --comsol 'C:\Program Files\COMSOL\COMSOL64\Multiphysics' --jdk C:\Users\Everwalker\jdk11 --fixture C:\task\W22Fixture.java --spec C:\task\benchmark_spec.json --full
```

Run independently with COMSOL63 and `--version 6.3`. Without `--full`, the runner solves the20 mm baseline. `--candidate` accepts JSON with numeric L(m), p0(W per center emitter), p1(W per inner-ring emitter); it is intended for an independent fresh returned-candidate solve. The outer-ring power follows the frozen total-power equality. The generator supports explicit ring counts/masks/distances; this benchmark's three-group fixture is explicitly scoped and does not claim to fit an arbitrary measured source.

The full search runs27 unique native candidates. Each case is an individual bounded W21 request using the exact source hashes, actual stored time axis, native area-coupling values, units and producer identity. A cache record does not restore its historical native solution. Therefore the selected best is actually solved again before the final field sample, images and MPH are saved. Same-source cache reuse must consume zero additional solves; changing a value in the same source filename and explicitly re-importing must dispatch a new solve and change measured absorbed power. The original source bytes are restored before final delivery.

For a new same-version Worker and new-path reopen without solving:

```powershell
& C:\task\venv\Scripts\python.exe C:\task\run_w22_native.py --work C:\task\reopen64 --version 6.4 --comsol 'C:\Program Files\COMSOL\COMSOL64\Multiphysics' --jdk C:\Users\Everwalker\jdk11 --fixture C:\task\W22Fixture.java --spec C:\task\benchmark_spec.json --reopen C:\task\full64
```

The saved MPH is hash-checked, copied to the new project, sampled from stored results, re-bound to regenerated identical local inputs, sampled again, and actual geometry/material/boundary/ROI settings are read back. Native integration summation can differ at machine roundoff; raw differences are recorded. No solve is dispatched by reopen. A new path and new Worker are separate claims from physical validation.

`summary.json`, `transcript.jsonl`, `progress.json` and per-run `package_origin.json` retain statuses and identities. `project/source/` contains deterministic external inputs; `project/w22_final.mph` and the native source/temperature PNGs correspond to `final_parameters` and `final_sample`. Failed attempts remain evidence. Do not treat an exit code, generated source image, wheel test, hash or cross-version agreement as scientific acceptance. The independent Reviewer applies the frozen tolerances against separate source/spectral references and performs fresh native returned-candidate tests.

`tools/collect_w22_evidence.py` exports terminal task runs only, including raw JSON/CSV/input/MPH/PNG and selected operation evidence, excluding credentials and runtime state. Run it to a NEW archive name, verify the archive and every manifest hash on import, and preserve existing evidence byte-for-byte. Final scoped approval is recorded only in the Reviewer final decision and W22 result ledger after all six deliveries are satisfied.
