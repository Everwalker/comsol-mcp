# 从干净目录恢复

1. 保持网络，使用新的包目录；`python tools/verify_package.py` 校验本包。
2. `python tools/bootstrap.py` 优先 Git，未发现 Git 时自动使用固定 GitHub ZIP。
3. `python tools/audit_repository.py --repo repository --output repository/docs/handoff_g3_7_windows_w20/SOURCE_AUDIT.json` 核对源码。恢复工具不会执行仓库程序。
4. 新增任务文档和 `RESTORE_RECEIPT.json` 放在 `repository/docs/handoff_g3_7_windows_w20/`，不覆盖根目录历史回执。
5. 在新 venv 中按实际 Windows 解释器安装依赖、执行 `pip check`、冻结版本。不要复制 Mac venv/class 文件或将 Markdown freeze 当作所有环境都可用的锁。
6. 恢复后所有工作从新 `repository/` 展开；包中的诊断工具可继续由包根调用。不读取旧项目或曾经私有的源码 SHA。

## 完整性与界限
固定 commit 与 tree 在 PIN 中。Git 方式校验对象和实际工作副本；ZIP 方式先重建 Git tree，再生成文件。行尾保持源字节，不为 Windows 自动改写 LF。符号链接以惰性文本保存，不执行链接。
归档被 LFS/export-ignore 改写、存在 Windows 路径冲突、超出预算、缺失对象或网络失败时必须报错并保留诊断，不静默换 main 或更旧源码。
网络途径仅用于取源码和合法依赖，不需旧本地目录。商用 COMSOL/JDK/许可证/登录凭据是外部前提，包不提供。
新编译缓存、文档索引和可重放模型可以重建；重建后的模型不是旧证据中同哈希的模型。

## 发布
完成后按仓库授权普通推送阶段分支，不 force-push。推送前检测远端是否前进，正常合并并回归，不覆盖别人修改。
没有 Git/推送权限不应阻塞本轮代码与本地验收，交付可审计补丁和 BLOCKED_SYNC。新 evidence 与 package 的源码身份逐文件绑定；无需让含自身 SHA 的提交形成循环证明。
