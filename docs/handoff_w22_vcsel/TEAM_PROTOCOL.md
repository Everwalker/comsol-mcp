# 主 Agent 开发 / 独立 Reviewer
主Agent负责实现、调试、开发测试和产物。不要额外分配独立Developer。Host必须实际创建独立Reviewer上下文，给予本轮冻结合同、候选代码位置、diff和证据路径。不能自己编会话ID或以同一上下文换标签代替独立审查。

第一次实现前，Reviewer核对六项交付与benchmark_spec的适用范围；主Agent与Reviewer确定一次数值实现参数/预算，冻结到JSON并哈希。原T016/T045语义不得改变；参考合成参数可在运行前一次性调整，说明理由，禁止看结果后改阈值。

主Agent交付候选 -> 记录commit+必要dirty manifest -> Reviewer自行读相关源码/原始记录、至少独立执行一项双版本最佳候选或代表case复算和角向/不可能功率负控 -> APPROVED/CHANGES_REQUIRED/BLOCKED_ENVIRONMENT/REVIEW_BLOCKED。
Reviewer默认不改生产代码，可以写独立检查脚本。每轮只审变更与冻结要求，已有无影响证据可继承并给理由。候选变更则相应批准失效，不要求无关文档变更全量重算。

同一缺陷重复两轮未解决，先缩成最小复现并换方案，不能不断全量重跑或放宽标准。不存在自动通过的轮数/时间阈值。缺真实Reviewer能力时，主Agent可完成实现与自测，但明确review阻塞，不能代签。
科学模型判失败可以是负控测试通过；报告必须区分二者。Reviewer批准不是全部平台/真实器件校准认证。
