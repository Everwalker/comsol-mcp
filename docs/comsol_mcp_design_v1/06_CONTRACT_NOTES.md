# 协议与配置补充说明

本目录的schema和YAML是拟议新系统的设计契约，不能直接粘贴进原版Hermes或原版comsol-mcp后期待功能出现。示例请求只做schema验证，没有在COMSOL执行。

## Schema边界

272个动作提供顶层输入契约；六个核心动作另有自包含schema。公共类型在common.schema.json。`ActionResult`与`CapabilityEvidence`有独立schema。

每个领域仍需依据目标版本为data输出、feature属性、manage子动作补充专门schema。`TypedValue`的shape/range、SelectionSpec的kind必填字段、函数的维度/单位/导数等需要语义校验；JSON Schema通过不代表COMSOL接受或物理合理。

`api_invoke`的declared_effect只是声明，后端必须重新判定，未知方法按写/高权限处理；绝不能相信模型把危险动作标为read就免审计。所有改模型或需一致保存/主机控制的模型级动作要检查revision和idempotency。COMPUTE在作业提交时固定来源revision，并禁止同server冲突写，任务完成再更新可见revision。

本目录`expected_revision`是本系统管理的修订计数，不是COMSOL原生跨客户端原子CAS。外部Desktop修改需要事件/快照核验；短事务无法取得独占时必须停止或显式降级，不能承诺不会丢更新。

## 取消契约

原生取消方法只在对应版本和运行模式实际验证后启用。job_cancel返回CANCEL_REQUESTED不等于CANCELLED；必须看到引擎停止证据。等待超时不退出Java调用，force stop只限已授权、可证明所有权的隔离server，且不等于保存当前解。

## 配置分层

control.example.yaml给通用控制进程；platform-overrides.example.yaml给平台发现/GUI适配；lan-relay.example.yaml给可通信但计算机不直接上网的部署。真实部署应由安装器验证合并后的配置并生成“有效配置”报告。

null运行期限意味着未设定引擎任务时长上限，不代表无资源/权限控制。内存/磁盘/并发资源预算和无进展告警仍有效；一律不要把RPC等待期限用作杀求解进程的期限。

## 更新策略

冻结MCP SDK、控制进程、worker协议、各平台依赖和runtime适配测试。更换版本先在分支和回归环境验证，再发布。COMSOL安装包、商业文档和JAR不作为公开分发的离线包内容；从用户合法本机安装读取。离线构件包含可合法分发的本项目与第三方依赖、版本锁定、来源和hash。
