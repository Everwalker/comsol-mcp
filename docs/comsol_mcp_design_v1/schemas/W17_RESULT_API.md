# W17 Result System API Reference

Baseline: `Everwalker/comsol-mcp@328202cb8b30df2f0368b4bc06c70e3c91b8bce1`
Module: `comsol_mcp/_g3_results.py`

---

## 1. Dataset Operations

### 1.1 `dataset.list`

**Effect**: READ

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `filter` | `object` | No | Optional filter: `{"type_id": "Solution"}` |

**Response**:
```json
{
  "datasets": [
    {
      "tag": "dset1",
      "type_id": "Solution",
      "solution": "sol1",
      "component": "comp1",
      "geometry": "geom1",
      "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": "dset1"}]}
    }
  ],
  "count": 1,
  "tags": ["dset1"],
  "read_errors": []
}
```

### 1.2 `dataset.create`

**Effect**: WRITE

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `tag` | `string` | Yes | Unique tag for the new dataset |
| `type_id` | `string` | Yes | One of `SUPPORTED_DATASET_TYPES` (see §7) |
| `definition` | `object` | Yes | Property key-value pairs to set |

**Minimal example**:
```json
{
  "tag": "cpt1",
  "type_id": "CutPoint3D",
  "definition": {"data": "dset1", "pointx": 0.005, "pointy": 0.005, "pointz": 0.0025}
}
```

**Response**:
```json
{
  "tag": "cpt1",
  "type_id": "CutPoint3D",
  "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": "cpt1"}]},
  "created": true,
  "applied": ["set(data)", "set(pointx)", "set(pointy)", "set(pointz)"],
  "failed": [],
  "readback": {"tags": ["dset1", "cpt1"], "type_id": "CutPoint3D"},
  "definition": {"data": "dset1", "pointx": 0.005, "pointy": 0.005, "pointz": 0.0025}
}
```

**Error conditions**:
- `TAG_CONFLICT`: tag already exists.
- `API_UNSUPPORTED`: type_id not in `SUPPORTED_DATASET_TYPES`.

### 1.3 `dataset.inspect`

**Effect**: READ

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `path` | `string` or `object` | Yes | Dataset tag or structured path |

**Response**:
```json
{
  "tag": "cpt1",
  "type_id": "CutPoint3D",
  "solution": "sol1",
  "component": "comp1",
  "geometry": "geom1",
  "properties": {"pointx": 0.005, "pointy": 0.005, "pointz": 0.0025, "data": "dset1"},
  "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": "cpt1"}]}
}
```

### 1.4 `dataset.update`

**Effect**: WRITE

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `path` | `string` or `object` | Yes | Dataset tag or structured path |
| `definition` | `object` | Yes | Properties to update |

**Minimal example**:
```json
{"path": "cpt1", "definition": {"pointx": 0.008}}
```

### 1.5 `dataset.remove`

**Effect**: WRITE

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `path` | `string` or `object` | Yes | Dataset tag or structured path |

### 1.6 `dataset.solution_indices`

**Effect**: READ

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `path` | `string` or `object` | Yes | Dataset tag or structured path |

**Response**:
```json
{
  "tag": "dset1",
  "solution": "sol1",
  "inner_solnum": [1, 2, 3],
  "outer_solnum": [1],
  "time_values": [0.0, 0.5, 1.0],
  "parameters": {},
  "study_type": "Transient",
  "is_time_dependent": true
}
```

---

## 2. Result Evaluation

### 2.1 `result.evaluate`

**Effect**: READ (creates/removes ephemeral numerical node internally)

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `spec` | `EvaluationSpec` | Yes | See §5 EvaluationSpec schema |

**Minimal example — real scalar integral**:
```json
{
  "spec": {
    "expressions": ["T"],
    "solution": {"dataset": "dset1"},
    "aggregate": "integral",
    "complex_mode": "real"
  }
}
```

**Response**:
```json
{
  "values": 150.0,
  "storage": "inline",
  "expressions": ["T"],
  "dataset": "dset1",
  "solution": "sol1",
  "complex_mode": "real",
  "is_complex": false,
  "aggregate": "integral",
  "denominator_measure": null,
  "axisymmetric": false,
  "axisymmetric_factor_applied": false,
  "axisymmetric_applied_count": 0,
  "total_elements": 1,
  "engine_error": null
}
```

**Minimal example — complex field preserve mode**:
```json
{
  "spec": {
    "expressions": ["emw.Ez"],
    "solution": {"dataset": "dset1"},
    "aggregate": "none",
    "complex_mode": "preserve"
  }
}
```

**Response** (complex preserve):
```json
{
  "values": {"real": 3.0, "imag": 4.0},
  "is_complex": true,
  "complex_mode": "preserve"
}
```

**Minimal example — axisymmetric integral (2πr applied exactly once)**:
```json
{
  "spec": {
    "expressions": ["T"],
    "solution": {"dataset": "dset1"},
    "aggregate": "integral",
    "complex_mode": "real"
  }
}
```

**Response** (2D axisymmetric model):
```json
{
  "axisymmetric": true,
  "axisymmetric_factor_applied": true,
  "axisymmetric_applied_count": 1
}
```

### 2.2 `result.at_points`

**Effect**: READ (creates/removes ephemeral Interp node internally)

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `spec` | `EvaluationSpec` | Yes | See §5 |
| `points` | `array[array[number]]` | Yes | Coordinate matrix (rows = points) |
| `coordinate_unit` | `string` | Yes | e.g. `"m"` |
| `frame` | `string` | Yes | e.g. `"spatial"` |

**Minimal example**:
```json
{
  "spec": {
    "expressions": ["T"],
    "solution": {"dataset": "dset1"},
    "complex_mode": "real"
  },
  "points": [[0.005, 0.005, 0.0025], [0.01, 0.01, 0.005]],
  "coordinate_unit": "m",
  "frame": "spatial"
}
```

### 2.3 `result.sample_path`

**Effect**: READ (creates/removes ephemeral Interp node internally)

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `spec` | `EvaluationSpec` | Yes | See §5 |
| `path_definition` | `object` | Yes | `{"kind": "line", "start": [...], "end": [...], "n_points": N}` |

**Minimal example**:
```json
{
  "spec": {
    "expressions": ["T"],
    "solution": {"dataset": "dset1"},
    "complex_mode": "real"
  },
  "path_definition": {
    "kind": "line",
    "start": [0.0, 0.0, 0.0],
    "end": [0.01, 0.0, 0.0],
    "n_points": 10
  }
}
```

---

## 3. Numerical & Table Management

### 3.1 `result.numerical_manage`

**Effect**: READ or WRITE (depends on `action`)

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `action` | `string` | Yes | `"list"`, `"create"`, `"get"`, `"inspect"`, `"update"`, `"run"`, `"remove"` |
| `path` | `string` or `object` | No | Tag for create/get/update/run/remove |
| `definition` | `object` | No | Properties, including `type_id` for create |

**Minimal example — create EvalGlobal**:
```json
{
  "action": "create",
  "definition": {"type_id": "EvalGlobal", "tag": "ev1", "expr": "T"}
}
```

### 3.2 `result.table_manage`

**Effect**: READ or WRITE (depends on `action`)

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `action` | `string` | Yes | `"list"`, `"create"`, `"get"`, `"clear"`, `"remove"` |
| `path` | `string` or `object` | No | Table tag |
| `definition` | `object` | No | Properties for create |

---

## 4. Field Export

### 4.1 `result.field_export`

**Effect**: READ (writes a local file artifact)

**Arguments**:
| Name | Type | Required | Description |
|---|---|---|---|
| `spec` | `EvaluationSpec` | Yes | See §5 |
| `format` | `string` | Yes | `"json"`, `"csv"`, or `"txt"` |
| `destination` | `string` | Yes | Absolute file path for the exported artifact |

**Minimal example**:
```json
{
  "spec": {
    "expressions": ["T"],
    "solution": {"dataset": "dset1"},
    "aggregate": "none",
    "complex_mode": "real"
  },
  "format": "json",
  "destination": "/tmp/field_export.json"
}
```

**Response**:
```json
{
  "file_path": "/tmp/field_export.json",
  "sha256": "a1b2c3d4...",
  "format": "json",
  "byte_size": 42567,
  "total_elements": 1500,
  "chunk_info": {"chunk_size": 65536, "total_chunks": 1},
  "evaluation_summary": {
    "expressions": ["T"],
    "dataset": "dset1",
    "solution": "sol1",
    "complex_mode": "real",
    "is_complex": false
  }
}
```

**Chunk verification** (T049):
```python
from comsol_mcp._g3_results import verify_artifact_chunks
valid, actual_hash = verify_artifact_chunks("/tmp/field_export.json", chunk_size=512)
assert valid is True
assert actual_hash == "a1b2c3d4..."
```

---

## 5. EvaluationSpec Schema

The `spec` object passed to `result.evaluate`, `result.at_points`, `result.sample_path`, and `result.field_export`:

```json
{
  "expressions": ["T", "u"],
  "solution": {
    "dataset": "dset1",
    "solution": "sol1",
    "inner": 1,
    "outer": 1,
    "time": 0.5,
    "frequency": null,
    "parameters": {}
  },
  "aggregate": "none",
  "complex_mode": "real",
  "units": null,
  "selection": null,
  "storage": "auto",
  "coordinate_frame": null,
  "weight_expression": null
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `expressions` | `string[]` | **required** | COMSOL expressions to evaluate (max 32) |
| `solution` | `SolutionSpec` | **required** | Dataset, solution, and index selection |
| `aggregate` | `string` | `"none"` | One of: `none`, `global`, `integral`, `average`, `minimum`, `maximum`, `std`, `rms` |
| `complex_mode` | `string` | `"preserve"` | One of: `preserve`, `real`, `imag`, `abs`, `phase` |
| `storage` | `string` | `"auto"` | `"inline"`, `"artifact"`, or `"auto"` (>1000 elements → artifact) |

### SolutionSpec

| Field | Type | Default | Description |
|---|---|---|---|
| `dataset` | `string` | **required** | Dataset tag (e.g. `"dset1"`) |
| `solution` | `string` | auto | Solution tag; auto-resolved from dataset if absent |
| `inner` | `int`, `int[]`, `"first"`, `"last"`, `"all"` | `"all"` | Inner solution index (1-based) |
| `outer` | `int`, `int[]` | `"all"` | Outer (parametric) solution index |
| `time` | `number` | — | Physical time for time-dependent studies |
| `frequency` | `number` | — | Frequency for frequency-domain studies |
| `parameters` | `object` | `{}` | Parameter name → value |

---

## 6. Complex Mode Transformations

| Mode | Output | Mathematical Identity |
|---|---|---|
| `preserve` | `{"real": r, "imag": i}` | Full complex data retained |
| `real` | `r` | Real part only |
| `imag` | `i` | Imaginary part only |
| `abs` | `√(r² + i²)` | `abs² ≈ real² + imag²` |
| `phase` | `atan2(i, r)` (radians) | `real ≈ abs·cos(phase)`, `imag ≈ abs·sin(phase)` |

Special values: `NaN` and `Inf` are represented as JSON `null` with an explicit `"engine_error"` field.

---

## 7. Supported Types

### Dataset Types (`SUPPORTED_DATASET_TYPES`)

`Solution`, `CutPoint3D`, `CutPoint2D`, `CutPoint1D`, `CutLine3D`, `CutLine2D`, `CutLine1D`,
`CutPlane`, `Join`, `Revolution2D`, `Revolution1D`, `Mirror3D`, `Mirror2D`, `Grid3D`, `Grid2D`,
`Grid1D`, `Edge3D`, `Surface`, `Volume`, `Parametric`, `Receiver`, `Average`, `Integral`

### Numerical Feature Types (`SUPPORTED_NUMERICAL_TYPES`)

`EvalGlobal`, `EvalPoint`, `Eval`, `IntVolume`, `IntSurface`, `IntLine`, `IntPoint`,
`AvVolume`, `AvSurface`, `AvLine`, `AvPoint`, `MaxVolume`, `MaxSurface`, `MaxLine`, `MaxPoint`,
`MinVolume`, `MinSurface`, `MinLine`, `MinPoint`, `Interp`

---

## 8. Aggregate Measures & Axisymmetric

| Aggregate | Numerical Feature | Denominator |
|---|---|---|
| `none` | `Eval` | — |
| `global` | `EvalGlobal` | — |
| `integral` | `IntVolume` | — |
| `average` | `IntVolume` (integral/measure) | `denominator_measure` reported |
| `minimum` | `MinVolume` | — |
| `maximum` | `MaxVolume` | — |
| `std` | `IntVolume` (combination) | formula documented |
| `rms` | `IntVolume` (combination) | formula documented |

**Axisymmetric discipline**: For 2D axisymmetric models, the `2πr` weighting factor is applied
exactly **once** by the COMSOL engine. The response reports:
- `axisymmetric: true` — the geometry is axisymmetric
- `axisymmetric_factor_applied: true` — the factor was applied in this evaluation
- `axisymmetric_applied_count: 1` — the factor was applied exactly once (never duplicated)

---

## 9. Ephemeral Node Lifecycle & Cleanup

Evaluation operations (`result.evaluate`, `result.at_points`, `result.sample_path`) create
temporary numerical nodes with unique tags (prefix `cmssp`). These nodes are:

1. **Created** before the engine evaluation call.
2. **Run** to produce results.
3. **Removed** in a `finally` block after data extraction.

If cleanup fails, the response reports:
- `cleanup.cleanup_failed: true`
- `status.execution_state_unknown: true`

These flags are **never downgraded** by outer success indicators (F05 escalation discipline).

---

## 10. Artifact & Chunk Verification (T049)

When `storage == "artifact"` or element count exceeds 1000:
- Data is written to a JSON file in `g2_artifacts/results/`.
- SHA256 hash is computed over the complete file bytes.
- `verify_artifact_chunks(file_path, chunk_size)` reads the file in fixed-size chunks,
  recomputes SHA256, and returns `(valid: bool, actual_hash: str)`.

This guarantees byte-for-byte reproducibility without truncated representations.
