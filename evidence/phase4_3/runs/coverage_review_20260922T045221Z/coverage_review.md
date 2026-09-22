# G3.3 numeric and binding coverage review

审查基准：本目录对应的软件工作树；未启动 COMSOL、未连接 live worker、未提交或推送。报告记录的是当前源码快照和离线契约测试，不能替代 fresh COMSOL 验收。

## 当前覆盖结论

- `result.evaluate` 的统计结果保持四轴 `expression,outer,inner,point`。最终统计值在选择器应用前重建 FieldArray，避免 `std`/`rms` 被旧均值数组覆盖：`comsol_mcp/_g3_results.py:3090-3128, 4460-4499`。
- 复数输出的 `FieldArray.is_complex` 表示已发布值的类型；`real/imag/abs/phase` 输出为实数，`preserve` 才保留复数：`comsol_mcp/_g3_results.py:3120-3131`。
- 轴对称权重只在原生数值特征 `set(intvolume|intsurface,"on")` 后通过 `getString` 成功回读时计数，并发布证据；不能由布尔请求直接宣布成功：`comsol_mcp/_g3_results.py:3133-3156, 3775-4180, 4549-4551`。
- `SolutionBinding` 保留真实 `(outer,inner,solnum)` 映射并规范化四轴数组；整数索引拒绝布尔、字符串和小数截断：`comsol_mcp/_solution_binding.py:65-70, 93-260, 370-620`。
- 数据集解析对所有类型 fail-closed。缺少 Solution dataset 的原生 `solution` 属性时，即使请求中传入一个存在的 solution tag，也不会把它当作绑定证明：`comsol_mcp/_dataset_binding.py:278-300, 390-420`; 调用方在 `comsol_mcp/_g3_results.py:798-828` 保留不完整 binding，`_coordinate_context` 拒绝继续。
- `result.at_points` 在读取任何数值前验证 `Interp.set("data", tag)` 的 `getString("data")` 回读；缺失、忽略或不匹配均为 `EXECUTION_STATE_UNKNOWN`，并在 `finally` 执行临时节点清理。成功响应包含实际 resolver provenance 和 feature readback：`comsol_mcp/_g3_results.py:4660-4749, 4852-4883`。

## 反例与验证

- `tests/test_g3_w17.py:1019-1145` 覆盖 at-points 数据集绑定 provenance、聚合 `getReal` 不能冒充多点数据、ignored setter UNKNOWN 与 cleanup。
- `tests/test_g3_results.py:739-760, 1501-1512` 覆盖缺少 dataset.solution 时显式请求仍拒绝，以及唯一 component/geometry 只在真实 solution 属性存在时使用。
- `tests/test_dataset_binding.py` 覆盖请求 solution 不能填补缺失 dataset property。
- 以下离线测试命令通过：

  `software_validation_venv/bin/python -m pytest -q tests/test_g3_results.py tests/test_g3_w17.py tests/test_g3_wiring.py tests/test_g3_3_core_numeric_contracts.py tests/test_dataset_binding.py tests/test_results_typed_crud.py tests/test_result_budget.py tests/test_coordinates_shape_bridge.py tests/test_probe_semantics.py`

  结果：`208 passed`。

  `software_validation_venv/bin/python -m pytest -q tests/test_g3_w17.py tests/test_g3_wiring.py`

  结果：`29 passed`。

## 当前快照 SHA-256

以下 hash 在报告写入时计算；若其他 agent 继续修改源码，应重新计算，不能把这些 hash 当作最终提交 hash。

```text
2e3a35c014539d0a974616c30bbb876a0e504325ce03beb58e5a591fb98e8fff  comsol_mcp/_g3_results.py
891f9a06595217d5101aa361cb60b53bb3a9f6b88a918bef41765b0f5f82627e  comsol_mcp/_dataset_binding.py
0815aac9d118d9c51e4eb22386bead9b00b59085c5aee08195e41a0324c42e2a  comsol_mcp/_solution_binding.py
b7ac2f3b15fb2f69f7db0d09bafedd77409a458fbfc9c7197616906337804fbd  comsol_mcp/_measure_spec.py
0adebdb27a8e9d28a0807e1908d44bd957d0c7f5523933fed94b86a382e173a0  comsol_mcp/_complex_transform.py
e9896f62ee6abba59336251ba38a6be9801c310e1676acf64fc7e37d570cad85  tests/test_g3_w17.py
8988e19af3e51b8faadad411e0ef4855b4f7bc8209bbc3fb538259efd8b73951  tests/test_g3_results.py
6cd1f80ea0d1b717688a0cb1695c42718162c9765e694e9c80b3d59c68d2873e  tests/test_g3_3_core_numeric_contracts.py
e2fa2f610e37eee583f97a09ee8b67f262c83affc160be764ae1f04dd2844063  tests/test_dataset_binding.py
```

`git diff --check` 对上述源码与测试文件无输出。最终 fresh numeric/full-suite 仍需在源码冻结后运行；本报告中的离线 PASS 不升级为 live COMSOL PASS。
