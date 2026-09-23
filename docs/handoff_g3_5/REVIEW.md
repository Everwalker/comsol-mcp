# G3.4 审查与 G3.5 推进建议

## 结论与范围

审查源：`Everwalker/comsol-mcp` 的 `20839628aa6f93272a463f4d88eb48704b971f87`；Git tree `2e72a4fa6eae809bbce92e4620592e6906d3e87b`；审查日期 2026-09-23。

**建议先完成 W18 定向修补，再进入 W19。不是全盘否定当前出图成果，也不能直接接受“18/18 全部 live PASS”的完整性结论。**

本次实际执行：GitHub 当前提交/台账/关键源码/验收驱动复核，九个隔离源码函数机制复现，工作包本地恢复工具验证。未执行：用户 Mac COMSOL、完整仓库 pytest、实际云端 Host、真实远端完整恢复。没有声称逐行审完仓库每个文件。

复现使用经本次读取的函数摘录和合成对象，不导入仓库，不连接引擎、不读取用户凭据。`review/probe_results.json` 的 `finding_reproduced=true` 表示风险被复现，不是产品测试通过。

## 一、应该保留的进展

- 本轮登记 13 个 W18 operation，静态面 67 个工具。
- 有 COMSOL 真实 1D/2D/3D 图、几何/网格、瞬态对比、保存后重开重绘的记录。
- V07 的图像确实经 stdio MCP 返回，并校验了 ImageContent 字节等于文件；只是执行服务为测试注入，并非全部正常冷启动链。
- 原项目根问题已有显式 `project_root` / `COMSOL_PROJECT_ROOT` 配置；Artifact 访问、四轴 CSV、artifact-only 响应已有定向改进，不能继续说完全未实现。
- 仓库报告软件测试 1846 passed / 1 skipped；这是报告数字，本次没有重新运行。
- 云端 Hermes 视觉接收仍清楚标注 `HOST_DELIVERY_UNVERIFIED`。工具发现成功不能消除这一限制。

来源：当前 `PROGRESS.md`、`evidence/phase4_4_acceptance.json`、`tests/run_g3_4_w18_acceptance.py`。每项仍需按记录实际证明范围使用。

## 二、优先修复清单

| 编号 | 优先级 | 本次观察/复现 | 修改建议 |
|---|---|---|---|
| F01 | P0 | `plot_render`、`plot_geometry_render` 新 staging 不存在时回退旧 target；P01 复现旧 PNG 被返回为本次产物 | 统一新文件归属和原子发布；旧 target 永不补位；对两个 render 路径补负控 |
| F02 | P0 | render 临时节点移除失败被吞掉；P02 返回图像而有残留节点 | cleanup/restore finally、失败升级、持久 job/dirty/图像状态一致 |
| F03 | P0 | render provenance 用 dataset.data 或 dataset tag 作 solution；P03 报告上游数据集而非实际 solver | 复用 W17 DatasetBinding/SolutionBinding，原生验证选中解与图像一致 |
| F04 | P1 | 路径只保留前两个层级；矩阵被变成字符串列表；P04/P05 复现 | 通用 typed NodePath/属性回读；未支持结构提前拒绝 |
| F05 | P0 | gateway 可对 failed/unknown envelope 返回 ImageContent；24字节假 PNG 通过头检查；交付错误丢 job identity | 有效科学图与诊断图区分；完整解码；保留原 job/artifact；不重复渲染 |
| F06 | P0-验收 | A05 缺顶层 success，错误响应仍满足“短且无 values”；P09 复现 | 调真实 result.evaluate artifact 路径，先检查 success/isError，再做预算与内容核验 |
| F07 | P1-验收 | A07 只数历史 184 个条目，没有比较本轮当前文件；run HEAD为7e029...，发布HEAD为208... | 本轮源清单前后与发布快照的逐文件对应；不武断认定运行源相同或不同 |
| F08 | P1-验收 | A01旧PIN矢量检查、A02MockWorker、V02–V06直接领域调用，V07注入服务 | 保留控制/native证据级别，增加真正源外安装+生产冷启动公开链 |
| F09 | P1 | export.run 已删除旧文件补位，但 filename恢复不在finally、恢复异常吞掉、覆盖默认真 | 与render共享发布/恢复服务，不用三套有差异的错误处理 |
| F10 | P1-交付 | Hermes工具数解析失败补67；V10无其他进程也PASS；README还称历史PID存活及无except:pass | 删除虚构兜底值，分列不适用/未测范围，生成当前状态而不手填 |
| F11 | 下一阶段 | 已有job status/log/result/reconcile、串行队列与超时告警，但公开控制面仍缺正式取消等能力 | 在现有基础上实现W19，不以停止等待或标志置位称引擎已取消 |

F01–F05 会影响科学结果或恢复判断，先修再扩大长任务能力。F06–F10 会让测试掩盖问题，需要同步修正。所有修复是下一 Agent 的任务，本包没有修改 GitHub 生产代码。

## 三、复现的边界

1. P01：模拟导出未写新 staging，预置有效旧 PNG；函数返回了旧 digest。不是 COMSOL 已实际产生旧图事故的证据。
2. P02：模拟临时节点删除异常，函数正常返回图像且未报告清理失败。
3. P03：数据集 `data=upstream_dset`、实际 `solution=sol_actual`，函数报告前者。
4. P04：请求 `pg/surf/deform`，解析只留下 `pg/surf`。
5. P05：`[[1,2],[3,4]]` setter 收到 `['[1, 2]', '[3, 4]']`。
6. P06：failed + execution_state_unknown 的 envelope 仍携带一个 ImageContent。错误图可用于诊断，但当前没有明确区分诊断与已验证科学图。
7. P07：24字节不完整 PNG 通过当前网关验证。
8. P08：图像交付拒绝时，原 job/operation execution 元数据丢失。
9. P09：A05式缺 success 请求被网关拒绝，却仍通过原预算断言。

## 四、为什么不是再次全盘重做

当前 native 图像生成是实际进展。问题集中在边界一致性：安全发布、绑定、错误传播、typed数据复用及验收器的假阳性。沿既有 W17 服务修 W18，比再造一组简化 wrapper 更有价值。

Gate A 通过后，按原计划进入 W19。此时重点从“能调用求解/绘图”转向“长任务可观察、可取消、断线可恢复、原作业不重复、同Server不并发破坏”。不提前展开 W20 物理验证或 W21 优化平台。

## 五、W19 实施重点

- 原 job/request/operation 身份、持久状态与已存在串行队列保持一个真相源。
- 排队取消与运行取消分开。`Future.cancel()` 只能处理其实际语义，不能等价于 COMSOL 求解终止。
- 运行中止候选要查已安装 build 的官方 API/签名并实测；原生协作取消、任务独占进程停止与不支持分别报告。
- 同一 Server 仍串行；状态和取消请求控制面及时应答，不保证未验证的原生cancel调用一定能越过引擎阻塞。
- 断连/崩溃优先协调旧 request IDs，不重放未知写操作；真正停止后才允许恢复与generation更新。
- 图像交付失败不引发新求解/渲染。已有产物用原job/artifact重新读取。
- GUI、Windows、6.3 或云端API key不可访问时单列，不编造认证，也不阻止能独立完成的本轮修补。

## 六、交付风险与恢复设计

本包是固定公开版本的联网恢复器，完整源码不内置。只有已提交数据可从该提交恢复；ignored/未保存模型、实验文件和许可证无法从hash或日志还原。构建器重新生成的是新模型，不是历史同SHA文件。

历史报告中的本地 tar.gz/bundle 名称不保证它们在GitHub可下载。本包不依赖它们。恢复后核对全部文件、记录新环境、用正常入口重新测试。发布只包含审计过的公开源码/证据，不将私有Git分支、凭据或COMSOL软件重新分发。

## 七、源码定位

全部定位均针对固定提交；详见 `SOURCE_MAP.json`：

- `_g3_w18.py`：`plot_render`、`plot_geometry_render`、`export_run`、`_resolve_plot_path`、`_apply_properties`。
- `_mcp_gateway.py`：`mcp_result`。
- `tests/run_g3_4_w18_acceptance.py`：A01/A02/A05/A07/V06/V07/V08/V10。
- `_control_daemon.py`：dispatch、_execute、_reconcile、_monitor、close。
- `_tools_control.py`：当前公开状态/恢复工具集合。
- 原 `04_IMPLEMENTATION_PLAN.md`：W19范围、依赖及验收编号。

## 八、证据可支持与不能支持的结论

可以支持：当前提交有原生绘图进展；指定函数有上述可复现控制流问题；验收器存在假阳性路径；W19需补正式取消与恢复语义。

不能支持：所有当前图像都是错的；实际用户模型已被破坏；9个合成探针等于完整COMSOL实机测试；云端Hermes已收到图像；其他平台或版本已全部认证。
