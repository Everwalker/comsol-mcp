# NEXT GOAL — G3.5：W18 定向收口，再推进 W19

## 0. 本轮任务、范围与前提

这是新的有效阶段任务：**固定公开源码恢复 → Gate A 修复 W18 已发现缺口 → W19 持久作业、取消、恢复与并发控制 → 有证据地交付并停止。**

- 审查基线：`20839628aa6f93272a463f4d88eb48704b971f87`。
- Git tree：`2e72a4fa6eae809bbce92e4620592e6906d3e87b`。
- 主开发环境：Mac；真实 COMSOL 版本以启动时发现为准，已有记录为 macOS Apple Silicon / COMSOL 6.4。
- 最终架构继续适配 Windows x64、macOS Apple Silicon/Intel、COMSOL 6.3/6.4；本轮不要求不存在的机器或许可证全部认证。
- 本轮不进入 W20–W26，不把 W19 扩成全新 Agent 平台。保留现有 W01–W18 成果，不重写已经有效的执行基础设施。
- 这是**联网恢复型包**。完整仓库不在 ZIP 内；使用包内脚本从锁定公开提交恢复。不得依赖旧项目目录、私有分支、旧 venv、历史活进程、旧凭据、旧聊天或包外同名文件。
- 用户授权的是项目内开发、测试与正常阶段交付；不是删除用户文件、杀掉共享服务、修改全局防火墙、修改系统权限或发布凭据。

已有原生出图、MCP ImageContent 和部分 Gate A 实现应继承。仓库的 `18/18 PASS` 不能直接解释为所有用例均为完整生产原生验收；`HOST_DELIVERY_UNVERIFIED` 继续有效，直到取得对应证据。

## 1. 开始前的唯一恢复入口

在**本工作包根目录**执行：

```sh
python3 tools/verify_package.py
python3 tools/bootstrap.py
python3 tools/audit_repository.py --repo repository --output repository/docs/handoff_g3_5/RECOVERED_FILE_AUDIT.json
```

以实际工具 `--help` 为准；恢复失败保留错误，不更换未经审查的 main，不覆盖旧目录。若选择其他恢复位置，给 bootstrap 和 audit 传入对应路径。

恢复后先完整阅读：

1. 本包 `REVIEW.md`、`ACCEPTANCE.md`、`RESTORE.md`、`SOURCE_MAP.json`、`PIN.json`。
2. 仓库 `AGENTS.md`、`CLAUDE.md`、根 `PROGRESS.md` 及 `docs/comsol_mcp_design_v1/PROGRESS.md`。
3. 原工作包 `05_DEVELOPER_TASK.md`、`01_ARCHITECTURE.md`、`04_IMPLEMENTATION_PLAN.md`、`03_ACCEPTANCE.md`、`06_CONTRACT_NOTES.md`。
4. `evidence/phase4_4_acceptance.json`、对应运行目录、`tests/run_g3_4_w18_acceptance.py` 和本轮涉及的生产代码。

`04_IMPLEMENTATION_PLAN.md` 中 W19 的原要求及 T022–T028、T053、T054、T057 仍然有效。本包是在该范围内补充修复顺序和验收方式，不删减原需求。

恢复脚本把本包任务文档复制到 `repository/docs/handoff_g3_5/`。在仓库内执行开发；本包根目录不是另一份生产代码库。新增进度维护在现有主进度文档及 `docs/handoff_g3_5/PROGRESS.md`，避免再建多套相互矛盾的主控文档。

旧文档“停在 W18”的句子作为历史停止边界保留；本轮明确授权完成 Gate A 后进入 W19。允许更新项目内 AGENTS/CLAUDE 的导航、当前范围和安装说明，但不扩大任何主机权限。

## 2. 环境与来源冻结

先记录实际 OS/arch、Python、pip、JDK、COMSOL build、产品和图形运行能力、源码路径、包安装路径、项目根及控制私有根。不得照抄历史机器路径、PID、端口或运行时回执。

- Python 依赖使用新 venv；核对现有 pyproject 和约束文件，不把 Markdown 环境列表当作可直接认证的锁文件。
- 不自动升级 COMSOL、Hermes 或系统 JDK。缺少公开软件依赖可联网安装到项目隔离环境，并记录来源和解析结果；商业软件/许可依用户合法安装。
- 先核验自有 Server 的安全启动方式，明确“任务拥有进程”不等于“网络隔离已证明”。复用已验证的安全方法，但旧回执不能用于本轮新进程。
- 稳定共享 Server 可供经授权的专用模型测试，不得删除用户模型、改全局配置或强制停止它。
- 测试前后生成实际源文件 SHA256 清单。`HEAD` 是源版本的辅助信息，工作树有改动时必须保留其清单和补丁摘要。
- 历史运行记录的 `commit_head=7e02986...` 与公开审查提交 `20839628...` 不同，不能仅凭 A07 的 184 个条目声称同源。比较真实文件；不推断历史运行一定没用到当前代码。

## 3. Gate A：先修 W18 的正确性与验收问题

### R01 — 三条输出路径统一新产物发布

涉及 `_g3_w18.py` 中 `plot_render`、`plot_geometry_render`、`export_run`，及 `_artifact_store.py`。

1. 删除“新 staging 不存在就改用既有 target”的逻辑。旧图存在、旧图有效、hash 可计算都不能证明本次渲染成功。
2. 对目标 COMSOL 版本和导出类型选择有效的文件属性；设置 staging 后必须权威回读一致，再运行。不把 setters 异常吞掉后尝试使用旧文件。
3. 每次运行使用唯一、受控 staging；记录创建与运行归属。已有 target 在失败情况下保持字节不变，不登记成新产物。
4. 复用统一的原子发布器。默认不覆盖；需要覆盖时使用明确布尔授权，不能用 `bool("false")` 或缺省 True。
5. 发布前再次核验目的路径、文件类型和并发出现的目标；no-clobber 不得用会覆盖竞态目标的 `os.replace` 冒充。
6. 不盲目重试可能已经执行的 create 重载。先通过文档/签名/隔离实验确定调用，再判断失败是否已产生节点。
7. 结果节点临时创建、临时设置的导出属性及生成文件要有统一生命周期；成功登记和失败清理都可追溯。

### R02 — 清理、恢复和错误传播

1. 临时 export 节点移除失败、staging 清理失败、导出属性恢复失败都必须上报，不允许 `except: pass` 吞掉。
2. 对用户已有 export 节点，在 `finally` 恢复**原始属性**，不是简单设成推断的 target；恢复失败保留原始故障和恢复故障。
3. 本轮发布文件已成功但后续登记/清理失败，返回“哪些文件已存在、哪些动作已完成、状态为什么未知”，不能返回“没有任何修改”或正常科学成功。
4. 统一通过已有 DomainOutcome / DispatchWitness / ExecutionService 传播 `partial_change`、`cleanup_failed`、`execution_state_unknown`，持久 job、MCP isError 和模型 dirty 保持一致。
5. 失败/未知结果默认不返回可被当作已验证科学结果的 ImageContent。确需诊断图时必须显式标注 `diagnostic` 与失败状态，不默认混入成功科学图。

### R03 — 科学绑定、类型和路径复用

1. 用已建立的 DatasetBinding / SolutionBinding 区分数据集上游引用与实际 Solver solution。不能再用 `dataset.data` 或 dataset tag 兜底填充 `solution`。
2. 图像 provenance 绑定完整 model_ref、操作与修订、数据集链、实际 solution、outer/inner、实际时间/频率/参数、表达式/单位、选区、图层和视图；区分 requested 与 verified。
3. `solnum/t/looplevel` 不得全部 `str(...)` 后假设成功。验证选中的存储解，图像和数值抽查必须来自同一解索引。
4. 对“修改参数但未重算”明确 stale policy；允许查看旧解时明确其来源，不标作新参数的计算结果。
5. 明确 `plot.render(options=...)` 的临时视图选择是否恢复、是否持久修改。未实现自动恢复前不得称纯读，写入路径仍走修订/权限/队列。
6. 复用通用 NodePath 和 typed property 系统。多层 `plotgroup/feature/subfeature` 要正确定位；不支持的深度或集合在写前拒绝，不截掉末级后修改父节点。
7. 数字数组、矩阵、布尔和 TypedValue 不能字符串化成 Python 列表文本；写后实际 getter 回读。视图/几何应保留 component scope，同 tag 不同 component 不得混淆。
8. 列出节点/属性时报告分页、未读属性与完整性，不将只取前 25 个字符串属性称作完整模型信息。

### R04 — 图像交付契约与预算

1. 输入 width/height/像素总数/格式/覆盖权限先验证，再启动引擎；字节预算同时区分原图、base64、文本镜像和内存。不要等读完整文件、复制整个 payload 后才做唯一限制。
2. base64 严格解码。PNG 必须可由可靠解码器完整解析，不只检查 8 字节签名和 24 字节头；拒绝截断、坏块、零维、解压炸弹与超预算图像。
3. 网关发生 MIME/格式/尺寸/解码交付失败时保留原 `job_id`、`operation_id`、`request_id`、`model_ref`、修订和可用 artifact reference。
4. 分开 `engine_execution_status` 与 `delivery_status`。引擎已完成但图像交付失败，不重新执行渲染来“重试交付”；通过原 job/artifact 修复读取。网关不能反向把持久引擎成功改成未执行。
5. ImageContent 字节须与登记产物 hash 对应；文本和 structuredContent 不复制原始 base64。延迟/超时后的 job_result 路径也必须有明确有界响应策略。

### R05 — 修复假阳性验收与源码对应关系

1. A05 当前人工 payload 缺少 `success`，网关生成错误响应后仍能通过“文本短且无 values”断言。新测试必须通过实际 `result.evaluate(storage=artifact)` 公开调用，先断言业务成功与 isError，再验证预算、完整 artifact 和数值/元数据一致。
2. 保留 malformed-envelope 作为**负向**测试，断言其失败，而不是将其计入预算成功。
3. A07 不能只要求旧 `publication_source_bridge.json` 长度等于 184。比较本轮运行源码前后清单、审查源码、实际发布源码；加入“改一个文件必须拒绝同源”的负控。
4. A01 不再仅核对旧 PIN 的字符串和几个 hash 示例；对本包 PIN 和全部恢复文件实际验证。不得覆盖历史恢复回执。
5. A02 必须真的在四个不同位置安装/启动/读写（程序安装 A、源码 B、项目 C、启动 cwd D），且走冷启动生产配置，不只构造 MockWorker 目录。
6. V02–V06 等直接调用领域函数可保留为 native adapter 测试；另加入正常 MCP → 自动控制进程 → 普通 server_connect/model_adopt → Worker 的非注入完整用例。V07 的注入服务不等于正常 cold-start 验证。
7. 所有 alias（`plot.render`、`plot_render`、`operation_call`）权限、类型、修订、作业归属和错误传播一致；不得测试分支走一套未公开调用链。
8. Hermes 的工具数解析失败不能兜底 67；未调用图像则只称发现/握手通过。云端凭据没有配置时保持 HOST_DELIVERY_UNVERIFIED，不从旧环境复制私密凭据。
9. V10 未发现任何其他 Server 时，不能说已证明保护了某两个历史 PID。保留“无对象”的结论；需要负控时创建本轮可管理但不属于待取消任务的独立 Server，测试不触碰它。

### R06 — 文档与交付收口

更新当前 README/PROGRESS 的有证据结论，保留历史文件不改写原始事实。当前章节不得继续混写旧 35/50/75 工具目标、Windows-only 安装和“所有 except:pass 已清理”等不实表述。工具数量从运行目录生成，不设硬编码成功阈值。

阶段末清单的每个文件要么真实在交付中，要么明确列为 private/excluded/missing with reason。只写在旧机器本地的 tar.gz/bundle 路径不等于已提供可恢复产物。

### Gate A 完成判据

R01–R04 的错误路径至少有正式控制测试和生产入口验证；真实 COMSOL 的成功路径与适合实施的负控通过。R05 修好后重新跑关键验收，保留 CONTROL / NATIVE / PUBLIC_MCP / HOST 区分。R06 给出当前证据与源码对应。

如果仅云端 Host、特定 GUI 或不存在的平台不可访问，记录限制后可继续独立 W19。若仍可能返回旧图、错误解、掩盖清理失败、错误重试写操作，则不得越过 Gate A。

## 4. W19：在现有作业系统上补齐取消、恢复和并发

### S01 — 状态与公开契约

复用 `OperationStore`、`ControlDaemon`、`PersistentJavaWorker`、ExecutionService；不要另建互不一致的第二个作业数据库或绕过 MCP 的执行入口。

- 对照动作目录补 job list/status/log/result/wait/cancel/reconcile 等必要能力；规范 alias，分页和每个状态的含义。
- 区分 QUEUED/RUNNING/终态，以及 CANCEL_REQUESTED/CANCELLING、UNKNOWN/RECONCILING。名称可沿既有规范，但语义必须独立。
- `cancel request accepted` 不是 `engine stopped`；`RPC wait expired` 不是 job failed；部分数值存在不等于正常 solve 完成。
- 相同幂等键+同请求复用原作业；键相同而请求不同拒绝；新的逻辑读取不能复用旧读取缓存伪装新观测。
- 每条事件保留 operation/job/request、source、observed_at 和来源级别，不能靠 elapsed time 推算一个虚假的进度百分比。

### S02 — 排队取消与正在运行的取消

**排队任务**：通过控制面原子仲裁 QUEUED→CANCELLED 与 QUEUED→RUNNING。确认没有对应引擎修改请求被派发后，才可标记排队取消成功。重复取消幂等，不能把正在运行的任务误归类为排队。

**正在运行的任务**：先调研已安装 build 的公开 API/可验证中止入口，做专用隔离 PoC。官方 `ModelUtil.showProgress` 的日志接口可以作为观察候选，但不是取消能力证明。不得凭记忆发明 `ModelUtil.cancel()`，也不得把提交到同一被阻塞引擎队列末尾的“cancel”当成及时中止。

允许明确区分：

1. 已实测的原生协作中止；
2. 显式项目授权下，对本任务独占、身份已校验的可丢弃 Server 执行进程级停止，并从检查点恢复；
3. 当前环境不支持的中止路径——如实 UNVERIFIED/UNSUPPORTED，仍保留 job 状态和恢复入口。

进程级停止不是原生 Solver cancel，报告中不能混称。默认禁止 force-stop；只有调用前明确取得 scoped 授权、确认 PID+创建时刻+命令/端口+所有权，且不会中断其他模型/任务时才可测试。不能杀共享 Server、按进程名批量 kill，或仅凭旧 PID 判定所有权。

要证明停止，就收集实际引擎任务结束/进程终止证据、取消后的模型状态、暂存产物状态与后续调用结果。不以 Future.cancel()、线程停止等待、标志置 true 或 RPC 报错代替。

### S03 — 崩溃、断连、检查点和状态协调

- Host/MCP 断开不自动终止作业；同一 job 可找回，不重复调用 solve/render。
- 控制进程重启优先协调已持久化的原 Worker request IDs；Worker 自身失联与 Server 仍在工作需要区别。
- 只能证明若干 API 调用已完成时，不强行把整个高层 callback 升级为成功。
- 检查点恢复前核验文件、hash、模型范围与执行静止状态；恢复后更新 generation，旧引用拒绝，并回读实际参数/节点/存储解。
- 恢复操作不假装能还原所有外部文件、GUI 状态或没有保存的解；报告恢复范围。
- 取消/超时正遇到保存或导出提交时，通过发布状态机判断结果；不能删除已经完整提交且属于原成功任务的产物。

### S04 — 并发、观察与预算

- 同一 COMSOL Server 的普通引擎请求统一串行，不能因为操作不同模型就并行写同一个实例。
- 状态、日志、取消请求入口不依赖长求解占用的普通队列才能应答；真正的中止仍要走经实测可用的执行路径。
- 不同独立 Server 可在资源与许可预算下并行；没有第二实例能力时该测试单列未运行，不编造并行成绩。
- 缓存健康状态要带时间、来源和 stale 标志；失联不能仍展示永久 READY。
- 验证无过载条件下状态接口延迟目标 p95<1s，保留原始采样、负载和测试条件。此数值是验收目标，不是既有成绩。
- 日志分页、日志轮转、数据库迁移、磁盘不足、空闲清理与 artifact/page 大小有界；不能把引擎内部内存估计称作实测峰值。

### S05 — 超时策略

保留并明确 RPC等待、排队期限、执行期限和无进展告警的区别。执行期限届满后的策略显式声明：告警继续、请求协作取消或授权的自有实例停止；没有成功中止就仍记真实运行/未知。

允许执行期限为 null；不得新增隐藏 120/300/3600 秒统一停止限制。慢机启动与长期求解是不同预算。关闭控制服务时的 detach/drain/cancel 策略也应明示，不用无期限 shutdown 等待伪装可恢复退出。

## 5. 实施关卡

| 关卡 | 执行内容 | 进入下一关的条件 |
|---|---|---|
| M0 | 恢复、来源冻结、复现探针、修补 Gate A、校正验收层级 | 关键 W18 正确性/安全问题有代码修复与实际可执行验收 |
| M1 | W19 schema/状态/队列取消/分页/幂等与持久化 | 控制层状态机、取消竞态和负控通过 |
| M2 | 原生运行、真实取消/协调/检查点恢复 | 对可访问范围取得实际引擎证据；未支持路线明确分列 |
| M3 | 长求解→结果→渲染→图像、断连重取、并发/资源测试 | 现有 W17/W18 不回退，原作业不会被重复执行 |
| M4 | 源外安装、新路径重建、回归、清理、证据和正常发布 | 当前源版本与交付一致，所有 required 结果有解释，干净恢复可复现 |

一个错误会污染后续环境时，先隔离/恢复该 case 再继续。不进行“每修一个地方就重跑全部用例”的盲目循环；先最小回归、再受影响范围、最后阶段回归。任务复杂不是停在计划的理由，实际修改代码、测试并交付。

## 6. 验收与证据要求

以本包 `ACCEPTANCE.md` 和原 T0xx 为准。所有新测试起始状态是 NOT_RUN。历史证据原样保留；新结果写入新的 run_id 目录，失败不覆盖。

至少记录：环境/构建、request/result、断言、引擎日志摘要、操作/作业身份、文件 hash、源清单前后、清理结果。私密详细日志可以脱敏摘要+hash 引用，但不能把未公开文件写成公共交付必备。

`review/probe_results.json` 是本次九个隔离 helper 复现，不是 COMSOL 测试。修复后可运行 `tools/review_probes.py --repository repository` 辅助比较。P09 只演示历史 A05 断言为何不充分，不是对新验收器的自动验收；需另测修好的实际 A05。`--expect-fixed` 只针对其他函数机制，不代替正式验收。接口变化导致探针不能运行时更新探针并解释，不能把无法执行算修复。

“实现完成”“控制测试通过”“原生调用通过”“生产 MCP 通过”“真实云端 Host 收到图像”“六组合认证”分开报告。mock 不能升级实机；没有其他 Server 的测试不能算跨实例保护实测。

## 7. 交付与停止

交付保留原源码、测试、修补说明、新证据、机器可读能力状态、当前阶段结果、下轮前置条件与可恢复工作包。正常阶段同步遵循仓库既有授权：先检查远端变化，普通非强制提交/推送；分支保护需 PR 则报告待合并。禁止覆盖远端用户修改或推送包含私密历史的 bundle。

公开工作包优先采用已经审计的公开提交+可验证源码快照/联网恢复器，不将未经审计的全部本地 Git 历史打包。生成归档前枚举 include/exclude 和全部 hash；若私密凭据已写入中间 commit，即使工作树已删除也不能直接发布该历史。不得自动执行凭据吊销或假称已吊销。

成功停止：Gate A required 修复通过，W19 当前可访问 Mac 范围有真实取消/恢复/状态/不重放与受影响回归证据，交付与源码一致，记录 `G3_5_MAC_W19_VERIFIED_SCOPED` 或同义限定状态后停止。共享 Server 原生取消或云端 Host 不可验证时须在能力表单列，不能被限定状态掩盖。

受阻停止：缺少真实必要许可/权限/环境且无可安全验证路线时，先完成所有不依赖它的本轮工作，留下明确阻塞、最小复现、已完成修补、待执行命令和证据；不得把受阻阶段称通过。只需要下一项实际授权时才说明，不能反复索取已经给过的项目内开发许可。

无论成功或受阻，都不得自动进入 W20，也不得后台轮询、设置自动任务或承诺稍后完成。
