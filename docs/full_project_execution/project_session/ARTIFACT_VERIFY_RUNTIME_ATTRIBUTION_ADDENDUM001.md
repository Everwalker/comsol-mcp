# Runtime attribution addendum for joint review run002

**Result: PASS — the pytest child runtime was Python 3.12.13.** This addendum corrects the meaning of the `runtime.python_version` field in the original `run002/TEST_RUN_RECEIPT.json`. That field captured the outer controller Python 3.14.7. The original receipt's `argv[0]`, `runtime.path`, and `runtime.realpath` identify the test child as the pinned Python 3.12 runtime.

I verified attribution with a separate metadata-only child using the same pinned executable path, `guarded_entry.py`, guard `sitecustomize.py`, and frozen candidate working directory. The control printed its own `sys.executable`, `sys.version`, platform, and resolved executable path. It returned 0; the guard recorded one active event, zero denied operations, and zero setup errors. The guard log was precreated with mode `0600`. The child reported:

- `sys.executable`: `/private/tmp/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python`
- `sys.version`: `3.12.13 (main, Mar  3 2026, 12:39:30) [Clang 21.0.0 (clang-2100.0.123.102)]`
- `realpath`: `/opt/homebrew/Cellar/python@3.12/3.12.13_2/Frameworks/Python.framework/Versions/3.12/bin/python3.12`
- `platform`: `macOS-27.0.1-arm64-arm-64bit`

The control used the exact run002 pinned executable and guarded-entry argv prefix. The original test receipt remains unchanged. This was a small runtime metadata probe only; it did not run pytest or repeat any product tests. The original joint test result remains 273/273 PASS, with 0 failures, 0 errors, and 0 skips.

Evidence hashes bind this addendum to the unchanged main report and test run:

- Main report SHA-256: `ffb77fb7e4653b2c0155fb79a9b260b385a736bcf97cc61aebed947b73c63a15`
- Original run002 receipt SHA-256: `415eda406f0f1b0a3fb5a110c0eaaecc8e5e7d8ffa7c2f137a42a836d64781b5`
- Original run002 JUnit SHA-256: `2b42f4fcd375906f448533f8ca13ba2ce9c430c0d9e68952f40810666232837d`
- Original run002 guard SHA-256: `e829f1d33850e6dcd129e9deed10365b7d3c160948e9f34529d10b776bf378ad`
- Runtime-control receipt SHA-256: `8b991c4b635e9730f816f7fcc1693ed8d306470618a42eb6ddc39bec0d303a55`
- Runtime-control stdout SHA-256: `b7f98504fd735c8422bebfa5a0007459ec3d2893c5e5bfb141a416ebbbbb52a0`
- Runtime-control guard SHA-256: `5fe1e68706df34cb62232f695cf44342944eb4bebd0325e9adb919bddee75385`
- Runtime-control guard source hashes: `guarded_entry.py` `e04b2cec6471e5b6ebb547bf6ede48ad7e6d497ee6048c720512fc6bba9a3242`; `sitecustomize.py` `479369dd916bb05f8a9e1bde27bb999de66a87051f54d2a607b3071b8b5025c2`

The C065 plus artifact.verify software-source review remains PASS. Native, COMSOL, JVM, and scientific acceptance remain NOT_RUN; no GitHub sync or promotion was performed.
