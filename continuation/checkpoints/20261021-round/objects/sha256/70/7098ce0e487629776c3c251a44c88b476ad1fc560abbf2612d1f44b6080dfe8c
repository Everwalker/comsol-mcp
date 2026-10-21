# 候选集成与可执行工作优先级

这是一份执行清单，不增加原验收条目，也不是自动合并程序。

1. `continuation/checkpoint/CURRENT_STATE.json`为当前快照，`PROGRESS.md`顶部是当前状态；下面ACTIVE/NEXT只是历史。新Goal恢复开发，不复用旧运行身份。
2. 用candidate_inventory生成字节差异辅助表；同时读取旧INTEGRATION_CONFLICTS。原candidate目录不改，修改只落当前规范生产树。
3. windows-r19：平台/资源/租约/ACL/恢复修复与既有公共路径协调；不要让W23分支覆盖它。以本轮diff决定受影响软件回归，然后做实际Windows目标测试。
4. w23-r15：采用比r14新的经审字段/strict-selection路径，保留r14失败和相应负控。将其公共Worker/结果改动与Windows分支逐项合并。源码通过不自动推导field值可用。
5. w24-coupled05：读取模型/阶段语义与依赖，只复用适用公共接口；固定科学假设和阈值不放宽。
6. W26代码位于packets/external-source和已发布相关路径，先依据EXTERNAL_SOURCE_MAP解析真实已包含源码。不要只用文件basename定位不同版本；用逻辑路径和hash。lock/下载来源维持既有固定输入，实际环境重建后再执行。
7. runtime identity：保留catalog/legacy与installation两个语义域。新增显式连接绑定/兼容测试，不造并行身份数据库或放开已有消费者。
8. 集成记录只需一份：origin(s)、选择/合并说明、修改路径、依赖、受影响测试、当前待测。一个文件未changed但依赖接口变了也需考虑回归。最终统一候选再由独立Reviewer复核。
9. 已接受的软件证据可以按源适用性继承；不同候选测试数不可相加当一个全量结果。当前旧source与新source关系明确，需重跑的范围必须实际运行。
10. 完成一个依赖段即推进原生小链或下一个可执行分支；不在集成表上无限添加字段、印章和签字。
