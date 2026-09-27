# `function.evaluate` value path — software implementation record

**Candidate state: `SOFTWARE_ONLY / NOT_RUN` for COMSOL runtime acceptance.** The implementation supplies an adapter over the documented `ParamBase` route. It has not been connected to a live COMSOL model in this work unit. The Java fixture compiles against the installed COMSOL 6.4 API/model JARs, which is static signature evidence only. COMSOL 6.3 and 6.4 runtime results, actual `functionNames()`/metadata behavior, and pre/post model identity/revision observations remain open.

## Frozen call contract

The top-level request remains the original `path`, `arguments`, optional `derivative` shape. No `function_name` or output-unit top-level field is introduced. `path` must resolve to a global `func:<tag>` or `component:<tag>/func:<tag>` node. The adapter reads exactly one actual name from that node's `functionNames()`; multiple entries return `AMBIGUOUS_FUNCTION`, zero/unreadable entries fail closed, and an unsafe returned name is unsupported. The resolved scope determines the generated symbol: `root.<actual-name>` globally and `root.<component-tag>.<actual-name>` locally. The function tag is not substituted for its actual function name.

Only `Analytic` and `Interpolation` nodes are sampled. Analytic arity is the length of the node's `args` property, read through its reported `StringArray` value type. Interpolation uses a positive `nargs` `Int` readback when present. If COMSOL reports `nargs` absent, the adapter supports only the documented local `source="table"` profile and requires exactly one entry in that node's read-back `argunit` `StringArray`, which establishes unary arity. It never infers arity from table dimensions or caller coordinates. Unknown sources, missing/malformed units, and non-unary table declarations fail with `API_UNSUPPORTED`; multiple `functionNames()` still fail with `AMBIGUOUS_FUNCTION` before sampling. See [`ARITY_AND_DERIVATIVE_RESEARCH.md`](ARITY_AND_DERIVATIVE_RESEARCH.md) for the version-specific documentation/native evidence and remaining validation boundary. The adapter never imports, refreshes, trains, runs, or solves.

Each sample has exactly one of:

```json
{"value": 1.25, "unit": "m"}
{"coordinate": [1.25], "unit": "m"}
{"coordinate": [1.25, 2.0], "units": ["m", "s"]}
```

`value` is unary shorthand. `unit` is accepted only for unary calls; multivariate calls use `units` with one entry per coordinate. If a sample omits its unit field, the adapter reads the function's `argunit` property, requires one safe unit string per argument, and reports `argument_units_source="function_argunit_readback"`. A caller can therefore distinguish a coordinate interpreted using the function declaration from one supplied with explicit units.

Numbers are finite JSON numbers, never booleans or expression strings. Unit strings pass a closed grammar for unit identifiers, `*`, `/`, integer powers (including signed exponents) and parentheses, then each non-dimensionless token is checked with `model.param().evaluateUnit("1[unit]")`. Numeric exponent tokens are matched greedily, so `m^10`, `m^+10`, and `m^-12` remain single powers. The checked token is inserted only as a unit suffix. A unit preflight failure leaves that sample `NOT_EVALUATED` and counts as an incomplete sample.

For each attempted sample the adapter calls `model.param().evaluateComplex(expression)` and requires exactly two finite numeric components. It preserves both as `{real, imag}`; there is no real-only fallback and no inferred zero imaginary part. It then calls `model.param().evaluateUnit(expression)` independently, including when a completed complex evaluation fails, and reports that output unit verbatim (or null). A terminal Worker `FAILED` reply with no unknown marker is a sample error. The Worker now has one narrow typed boundary for the directly observed COMSOL range tag: only `ModelParam.evaluateComplex(String)` throwing the exact `com.comsol.util.exceptions.FlException` with `getMessage()` equal to `Interpolation_function_is_out_of_range` is serialized as `FUNCTION_EVALUATION_ERROR`, with the classification predicates and native request ID retained as structured fields. The Python handler carries that Worker failure mapping through under `worker_failure_raw`; it does not use the tag to rewrite or clear Worker uncertainty. This Worker refinement has not yet been exercised in a new native candidate. A `JavaWorkerTimeout`, an explicit unknown error/code, or a returned `QUEUED`/`RUNNING`/`STARTING`/`IN_FLIGHT`/`PENDING`/`UNKNOWN` reply raises `EXECUTION_STATE_UNKNOWN` from this handler, preserving any available Worker status/request ID; it cannot be downgraded to a row failure or returned with `execution_state_unknown=false`. Successful and failed rows remain visible for completed calls. The action-level `status` is `OBSERVED` only when every sample succeeds; any completed sample error makes the outer operation `FAILED`. The separate `sample_completion` field is `SUCCEEDED`, `PARTIAL_FAILURE` for mixed successful/failed rows, or `FAILED` when every sample fails. This keeps a read batch's partial data distinct from a partial model mutation, while the public success envelope remains false for every incomplete batch. This operation makes no intentional model mutation, so its returned completed data reports `partial_change=false` and `execution_state_unknown=false`.

The classifier has an isolated Java negative-control probe in [`FunctionEvaluateWorkerClassificationProbe.java`](../../../tools/java/FunctionEvaluateWorkerClassificationProbe.java) and receipt [`worker_classifier_software_probe_01.json`](evidence/worker_classifier_software_probe_01.json). The actual production helper compiles with `--release 11` against the local COMSOL 6.4 API/util JARs and passes exact-positive plus receiver, method, arity, type, exception-class, subtype, and tag negatives without starting COMSOL. The probe explicitly constructs a dual `ModelParam`/`ResultParam` proxy and confirms that `ResultParam` is rejected even if a receiver were to implement both interfaces. This is static software evidence only; the Windows 6.4 candidate must still verify Worker serialization and the managed production response against the real server.

Every row and the action carry `range_status="UNKNOWN"`. For Interpolation nodes the adapter may include raw typed `argrange` and `extrap` readback under `range_evidence`, but these fields are marked `RAW_METADATA_NOT_NATIVELY_VERIFIED` and do not affect range classification. A successful evaluation is not proof that the point was in range.

A well-formed derivative request returns `API_UNSUPPORTED`. Native COMSOL 6.4 `ParamBase` probes of eight `subst(d(...))` and `substval(d(...))` first/second derivative forms all returned `Illegal_operator_context`; the current production action remains `NOT_IMPLEMENTED`. The documented `d()` operator may support a temporary Analytic-function expression, but no such binding has been natively tested and that route creates/removes model nodes, so it requires explicit mutation, revision, and cleanup semantics before production use. See [`ARITY_AND_DERIVATIVE_RESEARCH.md`](ARITY_AND_DERIVATIVE_RESEARCH.md).

## Versioned result schema

The data mapping uses `schema_version="comsol-mcp.function-evaluate/1.0.0"`. It reports the resolved `path`, actual `function_name`, `function_type`, scope and arity, `results[]`, `sample_failures[]`, counts, action `status`, `sample_completion`, and `range_status`. It does not embed a fixed native/evidence verdict in a runtime response. The per-sample failure collection uses a schema-specific name because the shared completion adapter reserves the top-level `failed` key for mutation/applied-count classification; successful read samples must not be represented as model mutations.

Each result has its input index and normalized numeric coordinates, bound argument units and source, the generated expression, independent `value_status`, `unit_status`, and `range_status`, complex `value` (null unless a valid pair was returned), output `unit`, and per-stage errors. API exception rows use `value_status="EVALUATION_ERROR"`; raw range data remains independent and cannot turn it into an out-of-range claim. Unit-token preflight failures use `argument_unit_status="INVALID_OR_UNVERIFIED"` and `value_status="NOT_EVALUATED"` because function sampling was not dispatched.

The managed completion adapter sees `OBSERVED` for a complete batch and `FAILED` for every incomplete batch, so it publishes `success=false` without classifying mixed read samples as a partial model change. `sample_completion` carries the row-level batch distinction outside the mutation-oriented completion vocabulary. `SOFTWARE_ONLY / NOT_RUN` is the evidence state of this work unit, recorded in this document and its test log; it is not hard-coded as a live operation result.

## Software verification

The targeted suite includes global/component same-name shadowing, complex values, unary/multivariate arity and unit policy, signed multi-digit unit exponents, ambiguity, malformed/non-finite/injected inputs, missing metadata, raw-only interpolation range evidence, mixed and all-failed sample batches, independent unit-read failure, completed Worker evaluation failures, Worker timeout/unknown/pending signals, unsupported derivatives, and no mutation/import/run fallback. It exercises the real `ExecutionService` inspect/read wrapper and managed G3 `function.evaluate` route, checking the public failed envelope and unchanged revision/dirty/fingerprint for ordinary sample errors. Actual `JavaWorkerError`, `JavaWorkerTimeout`, and unobserved RUNNING replies from `param.evaluateComplex` are also sent through the managed route and must freeze the ledger as unknown. A separate unverified-cleanup negative control confirms the existing unknown-state freeze remains active. It also retains the prior W13 refusal/validation tests.

Command used with the existing read-only Python 3.12 environment:

```sh
  /private/tmp/comsol-mcp-full-project-py312/bin/python -m pytest \
  tests/test_function_evaluate_contract.py tests/test_g3_w13.py \
  --junitxml=docs/full_project_execution/function_evaluate/run_w13_regression_07.xml -q
```

Result: **183 passed** across the new contract suite and existing W13 regressions ([JUnit](run_w13_regression_07.xml)); the standalone current contract suite passed **31 tests** ([JUnit](run_function_evaluate_05.xml)). These are software fixtures, not native COMSOL acceptance.

## No-solve native probe handoff

[`tools/java/FunctionEvaluateProbe.java`](../../../tools/java/FunctionEvaluateProbe.java) is a task-owned Java fixture with `run(Model, Map<String,Object>)`. On a fresh task-owned model it creates global and component-local analytic functions that both define `shared`, plus a complex analytic function. It samples with fully qualified expressions through `model.param().evaluateComplex()` and `evaluateUnit()`, then compares function/parameter/study/solver/dataset tags and function definitions before/after sampling. It creates no study, solver, dataset, plot, geometry, mesh, or solution and contains no solve/run call. Expected values for the first two samples are `1.01 m` and `2.01 m` from `x=1[cm]`; the complex sample is approximately `0.25 + 2i m`. The raw output must be retained with exact COMSOL build identity. This fixture itself has not been executed.

The source compiled on this Mac with Java 11 against COMSOL 6.4 Build 293's local API/model JARs:

```sh
javac -source 11 -target 11 \
  -cp /Applications/COMSOL64/Multiphysics/plugins/com.comsol.api_1.0.0.jar:/Applications/COMSOL64/Multiphysics/plugins/com.comsol.model_1.0.0.jar \
  -d /private/tmp/function-evaluate-probe-classes \
  tools/java/FunctionEvaluateProbe.java
```

Compilation did not launch COMSOL. The corresponding COMSOL 6.3 source/JAR compile and both 6.3/6.4 runtime probes remain `NOT_RUN`. When run, use a new disposable model/session owned by the native executor, invoke `FunctionEvaluateProbe.run(model, emptyMap)`, do not use Desktop/GUI, and preserve the complete raw result plus before/after revision/dirty-state evidence. The fixture-created component/functions are setup mutations; purity is judged only across the sampling interval after their setup snapshot.
