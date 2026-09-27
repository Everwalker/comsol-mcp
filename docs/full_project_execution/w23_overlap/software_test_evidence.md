# W23 software test evidence

Status: `SOFTWARE_ONLY`; native COMSOL/GUI and scientific acceptance: `NOT_RUN`.

The original schema 1.0.0 baseline remains preserved as the 20-case raw [`software_test_unittest.txt`](software_test_unittest.txt) and [`software_test_junit.xml`](software_test_junit.xml). The preceding 27-case planarity/phasor run remains in [`software_test_planarity_phasor_27tests_junit.xml`](software_test_planarity_phasor_27tests_junit.xml). The current definition schema is 1.1.0 and result schema 1.2.0; this ULP-bound run uses pytest 9.1.1 in the existing shared Python 3.12 environment and writes [`software_test_ulp_planarity_28tests_junit.xml`](software_test_ulp_planarity_28tests_junit.xml). No package was installed and the environment was not changed. The exact new pytest command is:

```sh
PYTHONDONTWRITEBYTECODE=1 /private/tmp/comsol-mcp-full-project-py312/bin/python -m pytest -p no:cacheprovider tests/test_mode_overlap.py --junitxml=docs/full_project_execution/w23_overlap/software_test_ulp_planarity_28tests_junit.xml
```

The current suite passed all 28 tests. Earlier 20-case and 27-case logs are retained as historical evidence for their respective software-only contracts.

Coverage includes analytically checked identical modes and power, half-field amplitude with `normalized_overlap=1` but `eta_mode=0.25`, independent signal/reference-mode scaling, global phase, orthogonal polarization, a displaced profile (`normalized_overlap=8/15`), missing versus explicit zero imaginary data, plane/coordinate/unit and shape failures, translated/rotated planar invariance under representation roundoff, a 0.5 nm warp rejection at micrometre scale, nonfinite geometry refusal, the 0.1 m planarity reproducer, supported and mismatched phasor/time conventions, refusal of unreconstructed envelopes, reversed/non-unit normals, zero/negative/near-zero powers, raw out-of-bound efficiency without clamping, separate aperture capture, and `W` versus `W/m` accounting. Schema files are compared byte-semantically with the runtime schema constants, and the result must serialize as strict JSON.

The current pytest-generated counts are recorded in [`software_test_ulp_planarity_28tests_junit.xml`](software_test_ulp_planarity_28tests_junit.xml). The preceding 27-case result and original 20-case JUnit/unittest artifacts are retained. Passing these checks validates only the finite software calculation and contract handling; it does not establish COMSOL API mappings, native field extraction, mesh convergence, physical validity, or desktop delivery.
