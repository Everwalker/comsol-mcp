# 当前进度 — 2026-10-27（r321）

通用 MCP 目标 **active / IN_PROGRESS**。273 actions / 60 tests / 427 子断言、适用平台原生链、真实保存及新 Worker 重开、两个未预设 licensed 任务族和 fresh 最终 Reviewer 均未完成；completed_groups=[]，heldouts 未选。案例不作主线。

**规范源码现为 0.1.12，attach-only 已完成 Main 集中验收。** 当前 74 项定向软件测试通过、1 项因无网络测试门禁未运行；不与此前私有 25 项或矩阵计数累加。控制函数与 Strong/Main 已审私有候选逐字节相同；三处版本号一致，52 项第三方依赖未变。默认 auto 和 Windows SCM 优先级保持；显式 attach-only 只连接既存私有 endpoint，健康预检失败不另启 daemon。提交后的 UNKNOWN 不重放。配置和六文件指纹见[源码证据](docs/universal_mcp_execution/evidence/control-attach-only-0.1.12-20261027.json)。POSIX 私有检查不是 Windows DACL 或无竞态证明，软件检查不是实际公开 MCP。

0.1.12 对应构件尚未构建；本阶段按现有持续授权发布这六文件、Main3 和紧凑证据，再把实际提交与远端逐字节收据交给同 terra 绑定 R6 新命名空间。Main 核验冻结材料后才放行一次 canary；实际全部通过后才放行一次 build。

上一版 0.1.11 /592471c5 的 R5 wheel 和 Win/ARM/Intel 三包已 Strong/Main 字节接受。Mac ARM64 Q6-r4 正常离线安装→漂移负控拒绝→ready plan→确认卸载，原 Tool/driver 0；1805 SourceBase、199 安装包文件、4847 owned inventory 和父完整环境检查保持。此限定正常流程已发布于 14037da7，并独立逐字节读回；不重跑成功流程，不把它当 0.1.12 或 Core/native 证据。

实际 installed MCP initialize/tools/list/call/分页/负控/静态 runtime 仍 **NOT_RUN**。交互 owner 源码准备目前 PARTIAL，尚缺真实安装衔接和异常分支；先完成新源码构件，再继续无 SDK 自动 kill 的原 owner 路径。

Windows 普通 fixed-X 后端已获 Strong/Main 源与软件限定接受（95 current fake、71 direct injection entries，另 35 overlapping 不累加）；r2 有限执行包补齐 READY actor 绑定及实际终态数据，7+16 受影响 inert 检查通过，独立复审中。**没有 Windows 实际运行放行。** R23r3 native 仍 UNKNOWN，254f 及全部旧 owner 零后续 SDK/query/restore/cleanup/retry/adopt。

Mac Core 最近 strict-free 142901248 B，原 Server 2 GiB / Worker 1 GiB 门槛仍 BLOCKED，不降门槛、不重复空查。续跑见 RESUME.json.current_checkpoint_r321；未满足 required 和最终独立验收时不报完成。
