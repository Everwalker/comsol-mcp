# 清洁目录恢复与候选重建

## 固定来源
PIN.json指定公开commit/tree。优先Git恢复原始blob，不执行hooks或仓库代码；无Git走固定codeload ZIP并核对完整tree。预算限制、LFS指针、子模块、不安全Windows路径和不完整归档均明确报告，不静默省略文件。不调用main浮动链接替代。

恢复到全新repository/。已有目标拒绝，不reset/clean。历史symlink按原字节保存为惰性文本，不指向旧机private/tmp。原tracked文件字节不改；新增本轮overlay后以RESTORE_RECEIPT记录原始文件哈希和新目录。

## 候选不等于已经应用
根目录是当前基础源码。continuation/candidates下的源码保持独立，candidate_inventory只比较，不能执行或自动合并。恢复全部Git文件仍未生成可直接认证的最终release；必须按MASTER_GOAL集成。

`EXTERNAL_SOURCE_MAP.json`、`REVIEWED_MATERIALS_LOGICAL_MAP.json`将旧逻辑路径对应到本次已提交source；只使用经过校验的published路径，不重写旧证据里的路径字符串来假装旧机存在。

`LOCAL_ONLY_MANIFEST_R2.json`列出的仅本地文件并未被下载；其中依赖可按固定官方来源重新取得，测试模型可按公开builder新建。用户唯一数据和历史私有回执不能凭空重造，记录不可恢复范围。不要要求“恢复旧SHA文件”而无限重试已删除文件。

## 环境
新建venv/JDK编译缓存/COMSOL session；不从旧/private/tmp拷贝执行环境。重新读OS/arch、build、路径、许可、ACL/TCC与runtime端点。旧一次性入口需重建当前上下文、source和最小安全授权，不照抄先前approval hash。

当前主机可以是Windows或Mac；不假定必须SSH到另一机。不可用目标按原scope独立排程，其他安全开发继续。用户Mac6.3 skip不改动。

## 同一Goal续跑
已有V3 state时只读并继续，不重新初始化。原矩阵或scope文件与固定合同不一致时保留它们，做一次明确差异核对，不覆盖为空表。阶段完成不是停止条件；只有最终接受、真实外部阻塞或宿主会话限额暂停。

## 发布
从最终明确的规范源码和获准证据构建交付，不把未合并candidates或整个private runtime目录默认装入wheel。若需要离线Git bundle，先核对是否完整而非shallow/有外部依赖；源码快照不能宣称恢复完整历史。不要对未提交数据作备份承诺。
