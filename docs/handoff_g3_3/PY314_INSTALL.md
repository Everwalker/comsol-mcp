# Python 3.14.7 macOS arm64 installation

`constraints-macos-arm64-py314.txt` is the machine snapshot for the fresh
G3.3 software validation environment.  It records the exact distributions
observed on CPython 3.14.7 / macOS arm64 and is applied to the local checkout;
it does not contain `-e .`, a Git URL, or an absolute path.  The older
`constraints-macos-arm64-py313.txt` remains historical and is not rewritten.

From a clean checkout, verify the interpreter before creating the environment:

```sh
python3.14 --version
python3.14 -c 'import platform,sys,sysconfig; print(sys.executable); print(platform.platform()); print(platform.machine()); print(sysconfig.get_platform())'
```

The expected values for this lock are `Python 3.14.7`, `arm64`, and
`macosx-27.0-arm64`.  Create a new environment and install the local source
with the exact runtime/development constraints:

```sh
python3.14 -m venv .venv-py314
.venv-py314/bin/python -m pip install --constraint constraints-macos-arm64-py314.txt -e '.[dev]'
.venv-py314/bin/python -m pip check
.venv-py314/bin/python -m pip freeze --all
```

The package name in the constraint is satisfied by the explicit local
editable install.  Do not replace `-e .` with a PyPI package of the same name.
Keep the complete command output, interpreter path, source commit/tree, and
the resulting freeze beside the run evidence.

For an outside-source package check, build a wheel from the reviewed checkout,
record its SHA-256, then install that exact wheel into a second fresh
environment.  The dependency constraint is still applied to the wheel install:

```sh
python3.14 -m pip wheel --no-deps . --wheel-dir /tmp/comsol-mcp-wheel
shasum -a 256 /tmp/comsol-mcp-wheel/comsol_mcp-*.whl
python3.14 -m venv /tmp/comsol-mcp-wheel-venv
/tmp/comsol-mcp-wheel-venv/bin/python -m pip install --constraint constraints-macos-arm64-py314.txt /tmp/comsol-mcp-wheel/comsol_mcp-*.whl
/tmp/comsol-mcp-wheel-venv/bin/python -m pip check
```

The wheel route tests package resources and import resolution outside the
checkout.  It does not provide a COMSOL installation, license, model, or live
engine acceptance.  `pyproject.toml` requests `setuptools>=68` and `wheel` as
isolated build dependencies; the validation venv did not install either into
its runtime site-packages.  Therefore this constraints file fixes the
resolved runtime/development distributions, while the wheel build backend is
an explicit separately recorded input.  A future hash-locked build must add
the exact build-backend artifacts and hashes rather than silently choosing a
different version.
