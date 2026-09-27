# function.evaluate metadata getter audit

This audit freezes the finite Java accessor set used by the current
`function.evaluate` value route. It does not approve any other Java method or
claim that an arbitrary model property is safe to query.

The live handler resolves global functions with `model.func(tag)` and local
functions with `model.component(tag).func(tag)`. It then reads `getType()` and
`functionNames()`. Analytic arity comes from `args` via `hasProperty(String)`,
`getValueType(String)`, and the type-selected getter. Interpolation arity uses
`nargs` via `getInt(String)` when available, with the documented table fallback
reading `source` and one `argunit`. Omitted sample units read `argunit`. The
raw interpolation range record reads `argrange` and `extrap`; the advertised
property type selects `getDoubleMatrix(String)` or `getStringMatrix(String)`.
Evaluation is through `model.param().evaluateComplex(String)` followed by
`evaluateUnit(String)`. The handler does not use the two-argument unit overload.

| API owner | Exact signature used | Handler purpose | Evidence / ledger classification |
|---|---|---|---|
| `FunctionFeature` | `getType()` | Resolve supported function kind | 6.3 and 6.4 `javap`; already classified as read |
| `FunctionFeature` | `functionNames()` | Resolve the actual exported name; reject zero/multiple names | 6.3 and 6.4 `javap`; exact zero-argument read rule |
| `PropFeature` | `hasProperty(String)` | Test whether metadata is present | 6.3 and 6.4 `javap`; exact one-string read rule |
| `PropFeature` | `getValueType(String)` | Select the typed metadata getter | 6.3 and 6.4 `javap`; exact one-string read rule |
| `PropFeature` | `getInt(String)` | Read `Interpolation.nargs` | 6.3 and 6.4 `javap`; current accessor allowlist; exact production dispatch covered by managed test |
| `PropFeature` | `getString(String)` | Read `Interpolation.source` and `extrap` | 6.3 and 6.4 `javap`; current accessor allowlist; exact production dispatch covered by managed test |
| `PropFeature` | `getStringArray(String)` | Read Analytic `args` and per-argument units | 6.3 and 6.4 `javap`; current accessor allowlist; exact production dispatch covered by managed test |
| `PropFeature` | `getDoubleMatrix(String)` | Read typed double-matrix metadata when COMSOL reports that type | 6.3 and 6.4 `javap`; exact one-string read rule |
| `PropFeature` | `getStringMatrix(String)` | Read typed string-matrix metadata when COMSOL reports that type | 6.3 and 6.4 `javap`; added as an exact one-string read rule; all other argument shapes stay fail-closed |
| `ParamBase` | `evaluateComplex(String)` | Evaluate the bound literal expression and preserve real/imaginary values | 6.3 and 6.4 `javap`; exact one-string read rule |
| `ParamBase` | `evaluateUnit(String)` | Read the expression's unit | 6.3 and 6.4 `javap`; exact one-string read rule |

The 6.3 and 6.4 Windows installations were inspected with the task-local
Corretto JDK 11.0.32.1 `javap` against each installation's own
`apiplugins/com.comsol.api_1.0.0.jar`. Their SHA-256 values and selected exact
signatures are recorded in
[`evidence/function_evaluate_getter_javap.json`](evidence/function_evaluate_getter_javap.json).
The versioned 6.4 API documentation independently identifies `getStringMatrix`
as `String[][] getStringMatrix(String name)` and says it returns the named
string-matrix property. The source is COMSOL 6.4 `PropFeature` API HTML, PDF
corpus relative path
`doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/api/com/comsol/model/PropFeature.html`,
chunk 23018, SHA-256
`befb8cc1c0e3df1ce74d2fa2cd1377e73d378e5c26170558e9f40dc255cc3bc3`.

The candidate-04 Windows 6.3 transcript exposed the defect: the actual
`argrange` value type was `StringMatrix`, the handler issued
`getStringMatrix("argrange")`, and the witness incorrectly called that exact
query a mutation. Its three successful interpolation samples advanced the
managed revision. The next call returned COMSOL's explicit out-of-range
`FlException`, but the witness pollution caused the managed result to be
reported as `EXECUTION_STATE_UNKNOWN`. Candidate 05 adds only the exact
`getStringMatrix(String)` classification and its malformed-signature negative
controls. A separate managed-route test verifies a completed terminal
evaluation failure with a clean getter witness remains `FAILED`; an explicit
Worker `execution_state_unknown=true` still freezes the ledger and is never
converted to a terminal sample error.

Candidate-05 offline evidence must remain bound to its own source snapshot and
JUnit report. Static API signatures, fixture tests, and the earlier candidate-04
native failure are not evidence that candidate 05 has passed Windows native
acceptance.
