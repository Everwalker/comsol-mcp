# Restore the W21 closure candidate

Scope: Windows COMSOL 6.3 build 290 and 6.4 build 293, with the same Python package. This does not certify W22 or complete W24/T047 domain physics.

1. Restore the original fixed source `c2ca5e40054fa023aafe3ae77120948063496728` into a new directory using the workpack bootstrap. Do not reset an existing checkout or overwrite scientific data.
2. Apply `evidence/w21_closure/delivery/w21_final_code.patch` referenced by `evidence/w21_closure/delivery/DELIVERY_RECEIPT.json` (the early `candidate_t11_code.patch` is historical and predates the final public-negative fix). Run `git apply --check` first. The final patch is checked against original PIN blobs and the current source byte-for-byte.
3. Build a wheel from that source with the project pyproject.toml. Keep the build source, Windows venv, private control, model project and engine runtime directories separate. The delivered wheel is the candidate identified by `CANDIDATE_T11_02_SOURCE.json`; read package_origin.json in each native run to match the actual installation.
4. Reuse an available Python 3.12 environment or create a private venv and install the wheel and its declared dependencies. This task used `C:\Temp\w21closure_20260925\venv`, an ordinary wheel installation, and `C:\Users\Everwalker\jdk11`. COMSOL installations and licenses are external prerequisites. Never copy control tokens, endpoints or old process IDs into a fresh run.
5. Copy `tools/run_w21_native.py`, `tools/java/W21Fixture.java`, `tools/java/W20RefinementFixture.java`, `tools/java/W20SolutionStats.java` and the wheel to a dedicated Windows test directory. Put the fixture files and wheel together, because the runner records their source/hash identities. Use a NEW `--work` path for every run.

Example invocation (PowerShell, substitute the explicit paths for your host):

```powershell
& C:\test\venv\Scripts\python.exe C:\test\run_w21_native.py --work C:\test\run64 --version 6.4 --comsol 'C:\Program Files\COMSOL\COMSOL64\Multiphysics' --jdk C:\Users\Everwalker\jdk11 --fixture C:\test\W21Fixture.java
```

Use COMSOL63 and `--version 6.3` for the other version. Add `--refinement` for the frozen W20 T11 six-level test; use `--reopen C:\test\run64` with a new work directory for a same-version fresh server/Worker reopen. The runner starts its own isolated runtime and stops only identities it owns. Its private loopback webbridge configuration does not change the installed XML.

Read summary.json AND transcript.jsonl. A process exit code, matching hash or numerical PASS cannot by itself certify physical validation. Deliberately rejected negative requests are test successes only when their expected refusal is verified and follow-up valid calls still work. Preserve prior failed runs.

The ordinary source package and exact fixture recipes are portable; Windows private runtime junctions, licenses, credentials and control directories are not delivery artifacts.
