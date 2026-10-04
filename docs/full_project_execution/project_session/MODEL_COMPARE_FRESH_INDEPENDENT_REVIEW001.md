# model.compare fresh independent review

**Review: PASS_SOFTWARE_ONLY. Native acceptance: NOT_RUN. Full scientific acceptance: NOT_ESTABLISHED.**

The frozen candidate is based on `07a29d855fe684322d095aef98021c874e1e6af3` with fingerprint `40e251ad1a25e1e1d619a14b04c018d72cf62f33a1495b933c02538ed7896cfa`. The frozen manifest SHA-256 is `d294f3e735d8592d3dea23d2da189e2b50c5a4711f193265b36afc65218aea84` and candidate patch SHA-256 is `ddeb750436daec7f8a786fddc3c0506ed17af106affe9ad249b9ef526be9ae83`. Independent post-run hashing confirmed all seven changed source files and the full 16-file source/test closure still match the freeze; the changed path set is exact.

The fresh isolated copy ran the producer's seven regression modules: **237 passed, 0 failed, 0 skipped** in 3.605 seconds. Four additional review controls passed through the real production `ManagedBackend`/`ControlDaemon` route: complete typed metric difference without a write ticket or revision bump, null metric yielding `INCOMPLETE`, permission rejection before Worker getters, and generation drift yielding persisted `UNKNOWN`, both ModelRefs fenced dirty, and `safe_retry=false`.

Both runs used the pinned Python 3.12 runtime and the strict network guard. `sitecustomize` recorded activation before `guarded_entry.py`; there were zero denied socket calls and zero guard setup errors. 8 artifact.list/artifact.inspect regression tests were included in the 237-test run.

The first sparse-copy attempt preserved its two failures (235/237). The cause was an omitted importlib-listed `_g3_ops` dynamic module in the reviewer copy. I added that source dependency closure only to the `/private/tmp` APFS copy; the next exact producer-suite run passed. Candidate and baseline source remained unchanged.

Evidence, JUnit, stdout, stderr, guard logs, source hashes, invocation records, and the reviewer control source are under this directory. The tests use synthetic typed receivers, so COMSOL/JVM/Worker native behavior remains **NOT_RUN** and scientific acceptance remains **NOT_ESTABLISHED**.
