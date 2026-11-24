# result.evaluate / result.at_points：dataset_binding 响应说明

本文根据本候选固定对照实现记录响应行为，帮助调用方解释数据集绑定证据。它不是版本化输出 schema，也不宣称已经完成 COMSOL 原生验收。

## 放在哪里、是否稳定

成功的领域操作数据中：

- result.evaluate 返回 data.dataset_binding。
- result.at_points 返回 data.dataset_binding，并另有顶层 data.binding_source，其当前值为 resolve_dataset_binding(model, dataset_tag)。

当前代码把 resolver mapping 原样附到数据响应。common.schema.json 的 EvaluationSpec 定义请求输入；静态 action catalog 文件仍是活跃的 operation 输入 schema、effect 与 metadata 来源。其 JSON 中保留的 `implementation_status: PROPOSED_NOT_IMPLEMENTED` 是旧静态状态值；registry 会依据已登记的实现把运行时状态动态映射为 `SUPPORTED_UNVERIFIED`。该状态只表示实现已登记，不代表 COMSOL 原生验收。两项操作的输出合同仍是通用 `ActionResult`，未定义 `dataset_binding` 专用子 schema。所以下表是**当前实现可观察到的字段说明**，不是跨版本兼容承诺。调用前仍应查看安装版本的 live operation_describe 输入 schema；客户端应容忍条件字段和后续附加字段，不要把当前对象当作封闭枚举。

一个成功解析的派生数据集响应片段如下（只展示相关部分）：

    {
      "data": {
        "dataset_binding": {
          "dataset": "cut1",
          "dataset_type": "CutPoint3D",
          "solution": "sol1",
          "component": "comp1",
          "geometry": "geom1",
          "dataset_chain": ["dset1", "cut1"],
          "dataset_edges": [
            {"property": "data", "from": "cut1", "to": "dset1"}
          ],
          "binding_complete": true,
          "sources": {
            "solution": "upstream dataset reference(s): ['dset1']",
            "component": "upstream dataset reference(s): ['dset1']",
            "geometry": "upstream dataset reference(s): ['dset1']"
          },
          "properties": {"data": "dset1"},
          "read_errors": [],
          "error": null,
          "requested_solution": null,
          "provenance": {
            "dataset_tags": ["dset1", "cut1", "other_dataset"],
            "solution_tags": ["sol1", "sol2"],
            "read_only": true,
            "mutation_methods_called": []
          }
        }
      }
    }

示例中的 tag 和链仅用于说明结构。dataset_chain 从上游到目标数据集；dataset_edges 保留每条 data / data2 解引用边。properties 是目标 dataset 节点上成功读到的 solution、data、data2、comp、geom 字符串属性，不是整个上游链的属性副本；其中 data/data2 是 dataset 引用，不应自行解释成 solver tag。

## 字段含义与条件

| 字段 | 当前返回含义 | 条件 |
|---|---|---|
| dataset | 本次解析的目标 dataset tag | 成功解析路径中为字符串 |
| dataset_type | 目标节点原生 getType() 读回值 | 未读到时可为 null |
| solution / component / geometry | solution 从目标属性或已验证上游绑定解析；component/geometry 也可来自属性或上游。仅在模型只有一个 component 且该 component 只有一个 geometry，或已有 component 下只有一个 geometry 时，才按 resolver 规则推导 | 无法确认时可为 null；不把 data 引用当作 solution |
| dataset_chain | 去重后的上游到目标 tag 顺序 | 解析失败时可为空或只含可确认部分 |
| dataset_edges | data / data2 引用边对象，含 property、from、to | 由数据集类型和可读引用决定 |
| binding_complete | resolver 对完整绑定的判定：solution、component、geometry 可确认，无读取错误，且所有上游子绑定完整 | true / false；有窄范围的 result.evaluate 例外，见下文 |
| sources | solution、component、geometry 各字段的解析来源说明 | 内容随直接属性、上游引用或唯一 component/geometry 推导而变；不保证每个键都出现 |
| properties | 目标 dataset 节点上成功读到的上述字符串属性 | 按 dataset 类型、原生属性可读性而变；不是固定完整属性集 |
| read_errors | 读取/校验错误对象列表 | 为空表示 resolver 未记录这些错误，不等于数值或物理结论正确 |
| error | 主错误对象，含 code、message，以及可有的 path | 完整解析时为 null；不完整时通常是对象 |
| requested_solution | 调用请求的 solution tag | 正常完成目标解析时存在；未指定时为 null |
| provenance | 本次 resolver 的读取范围与方式 | 正常完成目标解析时存在，详见下节 |

错误对象的 path 是条件字段。对象字段和 sources 文本属于当前实现细节，尚无输出 schema 版本约束。

### Provenance 与 tag 可见范围

当 resolver 已读出模型的 dataset 集合并解析一个存在的目标时，provenance 当前包含：

- dataset_tags：本次读取的整个 model.result().dataset().tags() 清单，不限于 dataset_chain。
- solution_tags：本次读取的整个 model.sol().tags() 清单，不限于当前绑定的 solution；若 solution tag 集合无法读取则为 null，并伴随读取错误。
- read_only: true 与 mutation_methods_called: []：只说明 **dataset binding resolver** 的元数据读取；不表示 result.evaluate / result.at_points 整个调用是纯读操作。

缺失目标 dataset 时，resolver 会在 dataset_binding.available_dataset_tags 返回同一完整 dataset tag 清单（前提是清单可读）；这个早退分支没有 provenance 或 requested_solution。更早的参数/Worker 读取失败也可能没有这些字段；UNKNOWN 会保留为 UNKNOWN，而不是保证返回完整 binding mapping。公开错误 envelope 是否包含 details.dataset_binding 取决于错误分支，不能假定所有失败都带它。

当前实现不对这些 tag 做截断、筛选或脱敏。它们是当前受管求值响应公开给获准调用者的模型 tag 元数据；本说明不将其推定为机密，也不改变现有字段或缩小其范围。需要限制这类可见性时，应另行定义项目权限与兼容合同。

## 权限与适用范围

- 两项操作的领域 effect 均为 EVALUATE；受管 EVALUATE 的基础项目权限是 `project_write`，仅有 `inspect` 不满足。受管路由还会检查绑定模型的函数清单：模型含 `External` 或 `MATLAB` 函数时，当前项目还须授予 `trusted_code`；函数清单无法判定时，调用可能在派发前被拒绝。请求仍须绑定获授权项目和正确的 ModelRef。
- EVALUATE 不等于纯只读：求值路径会使用临时 numerical feature，并尝试清理；清理失败或状态 UNKNOWN 时仍须按返回状态核对。provenance.read_only 只描述 resolver。
- result.at_points 当前只接受 frame="spatial"，且不支持按 time、frequency 或 parameters 匹配；它要求可用于坐标上下文的完整 dataset binding。
- result.evaluate 一般要求完整 binding。当前代码只在实时读取满足 `_confirmed_component_free_global_dataset()` 的 component-free `Solution` + `aggregate="global"` 判据时允许窄例外；该例外可在成功响应中保留 `binding_complete: false` 与 `DATASET_BINDING_INCOMPLETE` 诊断。这是代码分支判据，不是独立 COMSOL 原生结论或原生验收。不要仅凭 binding_complete 单字段推断该响应是否被接受。
- 这些字段是绑定元数据，不证明表达式数值、单位转换或物理模型正确。

## English summary

In the current staged implementation, successful result.evaluate and result.at_points data include the resolver mapping at data.dataset_binding; result.at_points also includes data.binding_source. provenance.dataset_tags and provenance.solution_tags expose the complete model collections read at resolution time, not only the selected chain or solution. solution_tags can be null when unreadable; early failures may omit provenance, and a missing dataset can return the full readable list as available_dataset_tags.

The static action catalog remains the active source for operation input schemas, effects, and metadata. Its JSON retains the legacy `PROPOSED_NOT_IMPLEMENTED` value, while the runtime registry maps registered implementations to `SUPPORTED_UNVERIFIED`; that status means registered, not natively validated. The output remains the generic `ActionResult` contract, with no `dataset_binding` sub-schema. The output object is not defined by a versioned response schema. Both operations are classified as EVALUATE, whose baseline managed project permission is `project_write`; models containing `External` or `MATLAB` functions additionally require project `trusted_code`, and an indeterminate function inventory may block dispatch. The resolver's `read_only` flag does not make the full evaluation call read-only. This note describes staged source behavior only and does not claim native COMSOL validation.
