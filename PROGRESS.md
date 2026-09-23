# G3.4: W18 真实绘图、导出与 MCP 图像回传 (COMPLETED)

- **验收日期**: 2026-09-23
- **验收目标**: `NEXT_GOAL.md` (Gate A 定向修补 R01–R05 → W18 真实图形、导出与 MCP ImageContent 回传)
- **权威结论**: **PASS** (`status: W18_API_VISUAL_VERIFIED_SCOPED`, `host_status: HOST_DELIVERY_UNVERIFIED`)
- **运行 ID**: `g3_4_w18_acceptance_20260923T011312Z` (耗时 75.00s)
- **停止边界**: 严格停止于 W18，未进入 W19–W26
- **提交版本**: 分支 `handoff/g3_4_w18`，基线 `31152904205834125776524f92288e18ba93b853`
- **GitHub 推送**: `git_push_executed: false`（按指示制作离线全量交付包，未执行远程推送）

---

## 1. Gate A (R01–R05) 关键缺陷修复

1. **R01 项目根与安装根彻底解耦**:
   - `ManagedBackend`、`ArtifactStore`、`JavaWorkerPaths`、`ControlDaemon` 支持显式 `project_root` 及 `COMSOL_PROJECT_ROOT` 环境变量配置。
   - 自动检测并安全拒绝 `site-packages` / `dist-packages` 作为项目数据根。
2. **R02 Artifact 访问与控制私有边界保护**:
   - `artifact.read` 默认强制限制为注册产物（`require_registered=True`），拦截对任意项目文件的未授权读取。
   - `ArtifactStore.resolve_safe_path` 严格拦截控制 prefs (`comsol_prefs`, `login.properties`, `comsol.prefs`)、运行时数据库 (`docs_index.sqlite3`, `transactions.json`)、Python 源码 (`comsol_mcp`, `*.py`) 及私有凭据。
3. **R03 CSV 完整四轴语义与双向重构**:
   - 导出 CSV 严格保留 `[expression, outer, inner, point]` 真实坐标标签、单位与物理空间坐标。
   - 复数显式分离为 `real` / `imag` 列，支持乱序与非连续 outer/inner 参数。
   - 提供 `csv_to_field_array` 实现 1:1 双向完整复原。
4. **R04 storage=artifact Wire 返回预算与哈希缓存**:
   - `_field_array_summary` 在 artifact-only 返回中剥离全量 `values` 与 `data`，仅提供轴尺寸、物理单位与有界 preview。
   - `_read_pinned_chunk` 引入 `(inode, mtime, size)` 哈希缓存，消除重复全量哈希 I/O 开销。
5. **R05 台账、公开来源收口与零漏洞扫描**:
   - 排除本地未公开历史分支依赖，全量 Git 对象通过敏感信息与凭据扫描。

---

## 2. W18 真实图形、导出与 MCP 图像回传能力

1. **图形节点与视图生命周期 (CRUD)**:
   - 原生支持 `plot.list`, `plot.group_create`, `plot.feature_create`, `plot.update`, `plot.remove`, `plot.view_manage` 以及 `export.*` 完整动作族。
2. **Fail-Closed 科学绑定与属性设置**:
   - 消除 scientific bindings 和 `_apply_properties()` 中的 `except: pass`，属性缺失或数据绑定失败立即报错拒绝。
3. **真实 COMSOL 渲染与物理验证**:
   - 3D 表面图 (`surface_3d.png`, 800x600 PNG, 76KB)
   - 1D 曲线图 (`line_1d.png`, 640x480 PNG, 11KB)
   - 2D CutPlane 截面图 (`cutplane_2d.png`, 7.7KB)
   - 几何与网格原生渲染 (`geom_render.png`, `mesh_render.png`)
   - 瞬态多时刻对比渲染 (`render_t05.png`, `render_t10.png` 经实测探针温度演化校验)
   - 存盘重开验证 (`saved_w18_model.mph` 由新独立 Worker 打开，不经重算即成功重读重绘 `reopened_render.png`)
4. **完整 Image 数据 Provenance**:
   - 图像返回包含完整元数据：`model_tag`, `plot_group`, `dataset`, `solution`, `solnum`, `time`, `looplevel`, `expression`, `unit`, `expressions`, `units`, `features`, `options`。
5. **原子导出与流式哈希**:
   - `export.run` 使用 `.staging_<uuid>_<name>` 临时文件、流式 SHA-256 计算、`os.replace` 原子发布并自动完成 artifact 登记。
6. **MCP 多模态 ImageContent 回传**:
   - 统一 PNG 格式，强制验证 PNG 魔数头、10MB 大小上限及 16M 像素总量上限。
   - 真实 Public Stdio 调用：`tools/call` → `plot.render` → COMSOL → `ImageContent` 原生回传。
   - 文本防膨胀：TextContent 仅包含结构化摘要，严格禁止将原始 Base64 塞入文本响应。
7. **宿主集成与作用域界定**:
   - 隔离 Hermes 环境通过 `hermes mcp add` / `test` 验证 stdio 传输与 67 项工具自动发现。
   - 云端 Hermes 视觉接收明确界定为 `HOST_DELIVERY_UNVERIFIED`（未配置云端 AI API key）。
8. **安全保障**:
   - 保护既有外部 COMSOL mphserver 进程（PID 16067, 16138 存活且未受任何干扰）。

---

## 3. 验收用例表 (18/18 PASS)

| 用例 ID | 验收范围 | 判据与结果 | 状态 | 耗时 |
|---|---|---|---|---|
| A01_SOURCE | SOURCE | 固定公开 PIN 校验与恢复算法 | PASS | 0.007s |
| A02_INSTALL_PROTOCOL_NATIVE | INSTALL+PROTOCOL | project_root 配置隔离，site-packages 明确拒绝 | PASS | 0.002s |
| A03_SECURITY_PROTOCOL | SECURITY+PROTOCOL | 合成 sentinel 保护，拦截 prefs/token/.env/源码 | PASS | 0.002s |
| A04_DATA_FOUR_AXIS | DATA | CSV 四轴标签/单位/空间坐标/复数 1:1 双向重构 | PASS | 0.002s |
| A05_PROTOCOL_BUDGET | PROTOCOL | storage=artifact wire 预算截断，保留完整元数据 | PASS | 0.002s |
| A06_ARTIFACT_CHUNKED | ARTIFACT | 分块流式读取与哈希缓存加速 | PASS | 0.001s |
| A07_EVIDENCE_BRIDGE | EVIDENCE | 184 项源码桥核对与公开提交追溯 | PASS | 0.018s |
| V01_CONTRACT_REGISTRY | CONTRACT | 13 项 W18 动作注册与回退效应声明 | PASS | 0.000s |
| V02_NATIVE_PLOT_CRUD | NATIVE | 3D 瞬态模型构建、CutPlane 及 1D/2D/3D Plot CRUD | PASS | 6.314s |
| V03_NATIVE_RENDER_SURFACE | NATIVE_RENDER | COMSOL 3D 表面与 1D 曲线实时原生渲染出图 | PASS | 0.777s |
| V04_NATIVE_GEOMETRY_RENDER | NATIVE_RENDER | 2D CutPlane 与原生几何/网格图像渲染 | PASS | 0.371s |
| V05_NATIVE_DATA_BINDING | NATIVE_DATA_BINDING | 瞬态两个解分别渲染与内部测温数值严格对照 | PASS | 0.303s |
| V06_NEGATIVE_CONTROLS | NEGATIVE | 错节点/错格式/覆盖保护 fail-closed 负控验证 | PASS | 0.008s |
| V07_MCP_IMAGE_CONTENT | MCP_IMAGE | 真实 Stdio tools/call 调用与 ImageContent 图像回传 | PASS | 0.851s |
| V08_HOST_SCOPING | HOST | 隔离 Hermes CLI stdio 握手与云端作用域界定 | PASS | 1.732s |
| V09_REOPEN_RENDER | REOPEN | 模型存盘重开，不先重算即成功重读重绘 | PASS | 2.544s |
| V10_JOB_SAFETY | JOB+SAFETY | 外部已有 mphserver 进程隔离保护 | PASS | 0.025s |
| V11_DELIVERY_CHECK | DELIVERY | 离线独立交付包制作、完整性校验与安全扫描 | PASS | 57.525s |

---

## 4. 离线交付包与独立恢复验证

- **交付包路径**: `../COMSOL_MCP_G3_4_W18_DELIVERABLE.tar.gz`
- **全量独立 Git Bundle**: `comsol_mcp_g3_4_w18.bundle`（完整历史，无需网络或上游仓库即可直接 `git clone`）
- **交付清单**: `DELIVERY_MANIFEST.json`（包含源码等价性桥接与全文件 SHA256 资产清单）
- **质量说明**: `DELIVERY_QA.md`

