# Phase 1 API evidence notes

These are planning observations, not engine acceptance results. All observations
below concern the local COMSOL 6.4 installation; do not extend them to 6.3.

## External Java client

The installed `bin/comsolclientpath.txt` lists 25 client JARs. All 25 exist under
`apiplugins/`; only 21 of those names exist under `plugins/`. Resolve the official
list against `apiplugins/` and record its hash in the runtime evidence. The local
external JDK is Amazon Corretto 11.0.31, arm64. Its availability does not prove
that a client has connected or completed a model operation.

## Server and Desktop modes

The local COMSOL 6.4 documentation describes `mphserver -graphics`, `-multi on`,
`-portfile`, and `-login auto`. The last option generates login information; it
does not disable authentication. The installed launcher also implements
`-prefsdir` and `-tmpdir`. Keep generated credentials private and out of evidence
bundles. Do not modify installation configuration to make the PoC work.

Desktop connection uses `mphclient -server <host> -port <port>`. A Java client
connection alone does not demonstrate that Desktop displays the same model.

Source: COMSOL 6.4, *COMSOL Commands on macOS*, local corpus document 6232,
chunks 19821–19823, relative path
`doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_ref_running.38.33.html`,
SHA-256 `36a9fa631e932121c1d6332d43dd86b433d80f237b88463d54544c310788500b`.

## Cancellation and progress

Inspection of the installed public `ModelUtil` and `SolverSequence` interfaces
did not expose a general `cancel`, `stop`, or `interrupt` method. This is an API
inventory observation, not evidence that a running solver has been cancelled.
Batch command options include cancellation, but their semantics apply to batch
jobs and cannot be assigned to a shared-server Java call without testing.

`ModelUtil.showProgress(filename)` directs progress to a client-side file.
Source: COMSOL 6.4, *ModelUtil*, document 4086, chunk 16699, relative path
`doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_general.47.15.html`,
SHA-256 `efba38a9c78d87e68e7306ced73ccc947c97ca43802bfb5b29045b45c56bb16a`.

Desktop's Cancel and per-solver Stop controls have different semantics; Stop may
return an intermediate approximation. Source: COMSOL 6.4, *The Progress Window*,
document 6359, chunk 20033, relative path
`doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_ref_solver.36.111.html`,
SHA-256 `f0d4de0c6c818f98cfe183e6637530212fdbb8c30b3681436b4cc0a65716669a`.

## Selection and variable safety

The installed `Selection` interface exposes `isInheriting()` and
`inherit(boolean)`, not the legacy helper's `isInherited()` name.
`LocalSelection` exposes `named()` and `named(String)`, while `all()`, `set(...)`,
and `entities()` are inherited. A method signature establishes availability
only: editability and readback must still be checked on a real physics object.

Variable groups use actual variable names as setter keys and `varnames()` with
`get(name)` for readback. The legacy global-variable list incorrectly addressed
the collection instead of the tagged variable group.

## Legacy MPh evaluation

The installed MPh 1.4 `Model.evaluate` creates temporary numerical nodes even
for default-dataset discovery and nonaggregated evaluation. Its field-evaluation
branch removes its temporary node only after successful result retrieval. A
failure in that branch can leave a node behind. W04 must own its temporary-node
lifecycle rather than rely on collection-wide cleanup or classify this path as
pure_read.
