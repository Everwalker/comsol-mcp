# W01–W04 execution and acceptance boundaries

This is the implementation-stage test plan. The original design files remain frozen.
W05 and later work packages are not authorized by this phase.

## Dependencies and evidence

W01 freezes the source and audits the legacy interface. W02 runs after W01;
W03 runs after W02. W04 requires W01 and is delivered in this phase.
An unavailable platform or version blocks its own integration cases, not
independent software development. A completed baseline audit does not make a
known failing protocol acceptance case pass.

Each integration case records environment, request, result, assertions, and
engine log. Keep failed attempts alongside later attempts. Record source hashes,
commands, timestamps, actual process ownership, backend, and model identity.
Unit/protocol results do not certify a COMSOL platform or version.

## W02 numerical smoke contract

Use an isolated, explicitly owned test server and a model bound to its MCP
backend. The reference recipe is a unit square with the stationary coefficient
PDE `-div(grad(u)) + u = 2` and homogeneous natural boundary conditions.
The exact solution is `u = 2`. Before running, set the maximum absolute error
threshold to `1e-8` for finite evaluated solution values. Preserve the actual
sample count, error, solution data, and saved MPH. If a different recipe is
necessary, record its equation and thresholds before that run.

Reopen the saved MPH in a fresh client/worker process and evaluate again. A ZIP
integrity check or successful save alone does not establish reopen acceptance.
Render a real result PNG and verify that it can be decoded and has nonzero
dimensions. Rendering success certifies the tested graphics path only.

Use the external JDK 11 and the installation's official client classpath for
the Java PoC. Record actual server and client versions; never relabel a bundled
JVM run as an external JDK 11 test. Probe products without checkout. A missing
product negative test requires a real, documented product identifier and an
actual unavailable product; invented identifiers are not license evidence.

T002 requires two real engine versions and cross-version requests. A single
version smoke test cannot satisfy it. Record each of the six target combinations
separately; unavailable machines and installations remain UNVERIFIED/BLOCKED.

## W03 binding and cancellation

Desktop binding requires a real window and bidirectional parameter changes
between Desktop and the backend on the same server model. Matching labels or
paths alone is insufficient. Do not replace an existing user's Desktop model.

Distinguish shared-server Java API, Desktop GUI, and batch cancellation routes.
Public API presence is not successful cancellation. CANCEL_REQUESTED is not
CANCELLED: retain the solver's end/exception and subsequent engine readback.
Never stop a user-owned server. Do not change system accessibility/security
settings to make a test pass; record unavailable access as BLOCKED.

## W04 safety regression contract

- T003: retain a first model's numerical nodes and plots while adopting/loading
  a second model; no implicit pruning on either main-model load path.
- T005: user numerical tags, expressions, table links, and table data survive
  max/min/average/integral and an invalid expression. Nonaggregated evaluations
  also clean up their own temporary nodes on failure. Never delete nodes by a
  before/after collection difference, which might include another client's work.
- T006: create two actual variables in one global group and one component group,
  update one, read names and expressions back, and evaluate. No accidental
  `name`/`expr` variables.
- T007: exercise physics-level and feature-level explicit/all/named selection,
  and accurately reject a noneditable inherited selection. Read back the actual
  resulting selection rather than echoing the request.
- T033: reject temporary-node evaluation under pure_read or use an explicitly
  isolated copy; ephemeral_mutation must be declared, serialized, and clean
  only its own nodes. Preserve primary and cleanup errors if both occur.
- Legacy core metrics must not evaluate hardcoded model-specific expressions.
  Without an explicit task metric definition, return an actionable failure.

Tests with fake engine objects establish control-flow regressions only. The
same safety assertions must run against every actually available COMSOL engine.
The final report keeps unavailable environments, known protocol defects, and
later-work-package limitations visible.
