# `function.evaluate` 的 COMSOL 6.3/6.4 API 实施方案

**状态：API 文档/静态接口审计完成；尚未实现、尚未启动 COMSOL、尚无原生运行证据。** 这份方案用于替换 W13 的无条件 `API_UNSUPPORTED`，不把通用 Java 可调用、文档存在或夹具测试写成 COMSOL 原生验收。

> Implementation note (2026-09-26): the value-only software adapter and its frozen request/result boundary are recorded in [`VALUE_PATH_IMPLEMENTATION.md`](VALUE_PATH_IMPLEMENTATION.md). The decisions at the end of this initial plan were resolved by the parent agent before implementation. This file remains the versioned API research record; the derivative and range semantics below still require native probing.

## 结论与实施边界

这是可修复的 adapter 缺口。6.3 与 6.4 的 `ParamBase` 都公开 `evaluate`、`evaluateComplex`、`evaluateUnit`，并注明表达式求值包含函数、参数与单位。COMSOL 表达式文档又明确：真正的函数只依赖其参数，可用于参数表达式；带组件作用域的函数可用完整 namespace 调用。因此第一候选路径是：先解析目标 `FunctionFeature`，从该节点实际返回的 `functionNames()` 取得函数名，再将有限数值坐标格式化成受限表达式，通过 `model.param().evaluate(...)` / `evaluateComplex(...)` 求值。这条候选不需要求解，也不需要临时 Evaluation、Grid 数据集或 Plot 节点；仍须在 6.3 与 6.4 原生 fixture 上分别验证表达式拼接、函数类型、单位和 side-effect。

`FunctionFeature` 本身没有任意坐标求值方法。两版官方接口仅列出函数名、导入/刷新/计算、画图等成员；本机 6.4 API JAR 的 `javap` 也没有 `evaluate` 或 `evaluateComplex` 成员。故不能把不存在的 `FunctionFeature.evaluate(...)` 包装成实现。

当前代码在 [`_g3_w13.py`](/Volumes/SSD/Comsol-MCP/COMSOL_MCP_FULL_PROJECT_WORKPACK/repository/comsol_mcp/_g3_w13.py:1364) 验证输入后，无条件在 1405–1414 行返回 `API_UNSUPPORTED`。`functionNames()` 与参数求值方法已在 Java worker allowlist 中（[`PersistentComsolWorker.java`](/Volumes/SSD/Comsol-MCP/COMSOL_MCP_FULL_PROJECT_WORKPACK/repository/comsol_mcp/worker_java/PersistentComsolWorker.java:62) 65–66 行）；W13 其他参数路径已使用 `evaluateComplex` 与 `evaluateUnit`。这意味着当前最小缺项是安全绑定器、结果契约和版本/原生验证，不是把 `evaluateComplex` 加进通用 worker allowlist。

## 版本化调用形状

对已解析的函数节点 `fn`，调用顺序建议如下。表达式样例展示目标形状，不是已执行的 COMSOL 命令：

```java
String[] names = fn.functionNames();
String selected = selectOnlyFrom(names, optionalFunctionName);
String qualifiedName = isGlobalFunction
    ? "root." + selected
    : "root." + resolvedComponentTag + "." + selected;
String expr = qualifiedName + "(" + formatFiniteArgumentsWithUnits(point) + ")";

// Real-valued result, only when this function's value kind is proven real:
double value = model.param().evaluate(expr, requestedOutputUnit);  // or evaluate(expr)

// Complex-capable/complex result:
double[] pair = model.param().evaluateComplex(expr, requestedOutputUnit); // or evaluateComplex(expr)
String naturalUnit = model.param().evaluateUnit(expr);
```

Public method signatures and behavior are documented for both versions: [COMSOL 6.3 `ParamBase`](https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/api/com/comsol/model/ParamBase.html) and [COMSOL 6.4 `ParamBase`](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/ParamBase.html). `evaluate(expression, unit)` converts a real scalar to the requested unit. `evaluateComplex(expression, unit)` returns a two-double `[real, imag]` pair in the requested unit; `evaluateUnit(expression)` reports the unit and may return null when there is no unit or the model does not use units. Use the complex pair as two explicit result fields; never discard the imaginary part or infer zero from a failed call. For a complex-enabled analytic function, its `complex` property is documented. For function kinds without an authoritative real/complex flag, fail closed until the native probe defines the dispatch; do not reinterpret an exception from `evaluateComplex` as an out-of-range result.

The function symbol must come from the resolved node’s exact `functionNames()` result. If the node yields one name, use it. If it yields multiple names, require a `function_name` selector and accept it only when it exactly matches one of those returned names; otherwise reject as ambiguous/invalid before evaluation. Never splice caller-provided expression text or an unchecked name into the COMSOL expression. Use the normalized resolved path to choose scope: global function `root.<actualName>(...)`; component-local function `root.<componentTag>.<actualName>(...)`. COMSOL documents `root.an1(x)` and `root.comp1.an1(x)` as distinct functions which can shadow one another, and true functions as usable in parameter expressions ([6.3 Functions and Operators](https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_ref_modeling.19.061.html), [6.4 Functions and Operators](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/comsol_ref_modeling.19.061.html)). Never resolve a component-local function as a global function by its base name.

Every coordinate must be a finite JSON number (not a bool, NaN, or infinity), then formatted by a numeric-literal formatter such as Java `Double.toString`; callers cannot supply coordinate expressions. Units must be parsed as unit tokens only, reject expression delimiters/operators outside the supported unit grammar, and be inserted solely as `number[unit]`. The current W13 input allows one optional `unit` per sample. That is ambiguous for multi-argument functions with different `argunit`s; the input/output schema should add a per-coordinate `units` array (same length as `coordinate`) or explicitly limit the existing field to single-argument functions. Read documented argument units from the resolved function node when available. COMSOL says user-defined functions apply their declared argument/output units during expression parsing; `evaluate(..., unit)` / `evaluateComplex(..., unit)` perform output conversion. Still test conversion with distinct argument units in both versions before accepting it ([6.4 Functions and Operators](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/comsol_ref_modeling.19.061.html)). `evaluateUnit` and successful numeric evaluation do not prove that an untrusted unit string is safe; the strict parser is required before expression construction.

`coordinate` length must match known function arity; scalar `value` is shorthand only for a unary function. Read the function’s documented arity metadata (`args`, `nargs`, or type-specific metadata) where available and validate `derivative.argument_index` against that same arity. For function kinds without stable arity metadata, initially limit support to fixture-verified types; do not rely on the engine error text to validate a request. The repository’s documented property inventory is in [`_g3_common.py`](/Volumes/SSD/Comsol-MCP/COMSOL_MCP_FULL_PROJECT_WORKPACK/repository/comsol_mcp/_g3_common.py:171).

## Derivatives and range status

The value path above does not establish a derivative route. COMSOL documents `d(expression, variable)` as a **symbolic** operator, and analytic functions expose `dermethod` plus manual `argders` `(argument, partial derivative)` pairs; neither source defines a Java method for evaluating order 1/2 at arbitrary coordinates. Do not substitute a sample constant into `f(c)` and then differentiate it: that can reduce to `d(constant,x)=0`, a false derivative. The native probe must first prove a supported symbol-binding mechanism that keeps the chosen function argument symbolic through differentiation and then evaluates at the requested point. `order=2` should mean the repeated partial ∂²f/∂arg_j² for the one `argument_index` field; mixed partials cannot be represented by the current contract. Return `API_UNSUPPORTED` for derivative requests until both the binding and output units (function-unit / argument-unit to the requested power) pass native tests. Do not call `FunctionFeature.run()` as a substitute: documented `run()` may train/compute Least Squares, Gaussian Process, Polynomial Chaos Expansion, and DNN functions, which is a separate stateful action.

The result needs separate `value_status` and `range_status`; a COMSOL evaluation exception is not proof of out-of-range. Map a thrown evaluation to `EVALUATION_ERROR` (with a safe engine error code), and set range to `UNKNOWN` unless authoritative function metadata independently resolves it. A successful evaluation is not itself proof of `IN_DOMAIN` because extrapolation may have been applied.

For 6.4 Interpolation, `argrange` is documented as a per-argument interval; combine that readback with `extrap` to distinguish a point inside the declared range from one outside it. If the point is outside and extrapolation is enabled, report `OUTSIDE_EXTRAPOLATED`; if `extrap=none`, the point is outside the declared interpolation range, regardless of whether the API throws or returns a nonfinite value. Preserve the actual value call failure separately. Do not use Analytic `plotargs` or built-in `plotlimits*` as domain limits: the docs define these as plot ranges. The COMSOL 6.3 `model.func()` page has no `argrange` entry, so the 6.3 adapter cannot claim this 6.4 metadata route from the currently verified docs; return `range_status=UNKNOWN` in that case unless a documented, native-verified source is added. See [6.3 function properties](https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_api_general.47.34.html) and [6.4 function properties](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/comsol_api_general.47.34.html).

## Results dataset alternative and mutation policy

The Results subsystem offers a documented alternative for visualization or dense sampling: Grid 1D/2D/3D can select Source=Function and a function, including global functions without a domain mesh. It samples a grid with bounds and resolution; it does not document arbitrary per-request coordinate vectors, so it is not a drop-in replacement for this action ([6.3 Grid datasets](https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_ref_results.37.054.html), [6.4 Grid datasets](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/comsol_ref_results.37.058.html)). `FunctionFeature.createPlot(tag)` explicitly creates a plot group **and a dataset** ([6.3 API](https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/api/com/comsol/model/FunctionFeature.html), [6.4 API](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/api/com/comsol/model/FunctionFeature.html)); do not use it inside this read-oriented action.

The preferred direct `model.param()` route creates no model-tree nodes, but its side-effect and model revision behavior still require a before/after native check. The catalog already classifies this as `EVALUATE` and warns that ephemeral nodes are not automatically pure ([catalog entry](/Volumes/SSD/Comsol-MCP/COMSOL_MCP_FULL_PROJECT_WORKPACK/repository/comsol_mcp/data/g2/02_ACTION_CATALOG.json:4931)). If a future adapter chooses a Results-feature fallback, treat feature/dataset creation as a model mutation: reserve collision-free tags, snapshot tree/revision, remove every created node in `finally`, verify their absence, and report `PARTIAL_FAILURE`/`EXECUTION_STATE_UNKNOWN` if cleanup or revision verification fails. The exact create/consumer/remove API sequence and revision semantics were not established in this read-only study; do not invent them here.

Do not implicitly invoke `importData()`, `refresh()`, `run()`, or `continueRun()` during evaluation. Those are explicit FunctionFeature operations that can load external data or do training/computation. If a function cannot be evaluated from its current verified state, report the API failure and leave the model unchanged; a separate action can load/recompute it.

## Minimum native fixture for 6.3 and 6.4

Run the same Java/API fixture independently against COMSOL 6.3 and COMSOL 6.4. A Python mock, javap, fixture value, or saved plot is not a substitute. Use an empty model with **no geometry, mesh, study, solution, result dataset, or pre-existing parameters**, record exact COMSOL build/API-JAR identity, and do not call any solve API.

1. Create a global analytic function `f(x,y)=x+10*y`, and a component-local `comp1.f(x,y)=100+x+10*y`. Read each node’s `functionNames()` and evaluate with fully qualified names. Check `(2,3)` returns 32 for `root.f` and 132 for `root.comp1.f`; this tests actual-name binding and prevents global/component shadowing.
2. Create an analytic complex function with a known nonzero real and imaginary part, enable its documented complex property, and compare the `evaluateComplex` two-double result at a known point. Also test a real-only function through the selected dispatch and verify both overload behavior and the exact output unit from `evaluateUnit` / unit conversion overload.
3. Create a unit-bearing analytic function with distinct argument units and a declared output unit. Evaluate equivalent coordinates supplied in two different compatible units; compare physical values and converted outputs. Reject an incompatible unit and a malicious unit string before the engine call.
4. Create 1D tabular Interpolation on a known linear table, with `extrap=none` and a second instance with `extrap=linear`. Probe one interior point, each exact endpoint, just-outside left/right points, read back range/extrap metadata, and capture the exact COMSOL result/error behavior. Verify that exceptions remain `EVALUATION_ERROR` while range classification comes from metadata.
5. Create two function definitions with multiple `functionNames()` (where supported) and ensure a name not returned by that node is rejected without attempting evaluation. Try NaN, infinities, booleans, too few/many coordinates, and unit/function-name injection strings; none may enter the API expression.
6. Create a bivariate analytic function `q(x,y)=2*x^2+3*x*y+5*y^2`. For every proposed derivative route, check ∂q/∂x=`4*x+3*y`, ∂q/∂y=`3*x+10*y`, and repeated ∂²q/∂x²=`4`; confirm the calculation stays symbolic until coordinates are applied. If no documented/supported binding passes, keep all derivative orders `API_UNSUPPORTED`.
7. Before and after successful and failing point requests, compare function properties, parameters, result dataset/plot tags, solution/study state, model revision/change notifications, and any file/training state. The value path passes only if it does not solve, change persistent model structure or silently call import/refresh/run. If native behavior changes a revision counter or hidden function state, record it and update the action effect contract.

The initial implementation subset should be the pure function types that pass this matrix. File-backed or trained functions stay unsupported unless already-ready state can be proven and sampling is side-effect-free. Preserve per-point input identity, value pair, unit, function type/name, and distinct `value_status` / `range_status` in the output schema. A result failure at one sample must not make other samples appear successful; define whether the action returns per-sample errors or fails the whole batch before wiring the MCP contract.

## Verified version comparison and evidence

| Evidence | COMSOL 6.3 | COMSOL 6.4 |
|---|---|---|
| `ParamBase.evaluate/evaluateComplex/evaluateUnit` | Official API exposes scalar, unit-converting, complex-pair, and unit-inspection methods. | Same official signatures and semantics; local 6.4 API JAR `javap` confirms them. JAR SHA-256 `9bdc47a9e320be5721956336f44f5afa4cb06a20cfa887bc7d32d1a837483a67`. |
| `FunctionFeature` direct coordinate evaluation | No such method in the official method table. | No such method in the official method table or local `javap`; includes `functionNames`, `run`, import/refresh and `createPlot`. |
| Function calls in parameter expressions and scope | Official expression reference says true functions can be used in parameter expressions and documents full namespace rules. | Same namespace/parameter-expression rule; 6.4 reference manual and API docs corroborate. |
| Interpolation range metadata | No `argrange` entry found in the consulted 6.3 `model.func()` reference; do not report metadata-derived range without a further verified source. | `argrange` is documented as per-argument range; can be read with `extrap` for range classification, subject to native verification of units/value semantics. |
| No-mesh result alternative | Grid dataset can source a function and sample it without a domain mesh; uniform/grid sampling, not arbitrary request points. | Same. |

Local 6.4 source provenance: COMSOL 6.4 corpus `ParamBase.html` SHA-256 `d7b6be3acb71d7ba99292c7740cad05a413a5503ba6c913aa976b4d0c788c9c9`, chunk 23001; `FunctionFeature.html` SHA-256 `c99bb2a0c8134c5d2845ed55e76f894875de1f830d3484719c4d6822d6c9ae5d`, chunk 22848; `model.func()` SHA-256 `c0b0ddce365ed8da5a8d8e45818ab0b056e552aaa01c99ca22e1f8be7c1e0edf`, chunks 16754–16755; `COMSOL_ReferenceManual.pdf` SHA-256 `3dacc33243911a98cbcac19c6b83c9b662dd0b7c56b880b3a9e6a2ab602e7106`, page 231/chunk 12315; Grid 1D/2D/3D SHA-256 `465e42348820fad3099a49654e3434e2e7d67a1f908086ecd824269017990c2d`, chunk 19443. The inspected local JAR was `/Applications/COMSOL64/Multiphysics/plugins/com.comsol.api_1.0.0.jar`; no COMSOL engine was launched. There is no local 6.3 JAR/runtime result in this evidence set, so 6.3 claims rely on official 6.3 API/reference docs and need a native 6.3 run before acceptance.

## Remaining decisions for the implementation owner

- Add optional `function_name` (strictly validated against the resolved node’s `functionNames()`) for nodes that expose multiple names; define whether omitted selector is accepted only for a single-name node.
- Clarify input unit syntax for multivariate samples (`units[]` by argument versus one shared `unit`) and the default for unitless coordinates. Freeze the finite-literal/unit-only parser and output unit behavior.
- Choose initial function type support from the native matrix; do not auto-import/refresh/train during evaluation.
- Freeze per-sample versus all-or-nothing batch failure semantics and the versioned output schema.
- Keep derivatives disabled until both versions prove a symbolic argument-binding route and unit behavior; keep domain status `UNKNOWN` wherever actual range metadata is absent.
- Bind native evidence to exact 6.3/6.4 build identity, raw request/response/error logs, pre/post model revision/tree evidence, and test artifact hashes. Current status remains `SOFTWARE_PLAN_ONLY / NOT_RUN` for native acceptance.
