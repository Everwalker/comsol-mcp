# 无旧项目目录恢复

固定公开源 `60464c85b429e649f4f80484473999e5365ad96a` / `9ed8d11dc0f4d04830823821cc5a73fa81d1ff67`。bootstrap使用新目录抓固定Git对象或固定HTTPS归档，验证完整Git树；不自动切换main，不执行仓库代码，不安装依赖，不启动引擎。恢复目标已存在就拒绝，不自动覆盖、reset或clean。Git不可用时 `--method archive`；若归档丢失export-ignore/LFS内容导致树不符，应改合法Git路线，不能跳过校验。

本轮overlay使用 `docs/full_project_continuation_v2/`，避免覆盖已有 `docs/full_project_execution/` 及数MB进度。init_resume核对原需求与当前scope decision，只建立新索引；不运行旧init_completion。较新源/本地修改存在时另开目录对比、保留，不强制回退。

原始冻结的六文件继承合同在inherited_contracts保持字节相同。ORIGINAL_REQUIREMENTS仍指原合同定义版本而非当前运行源码；二者用途不同。

缺少LFS backing、未提交科学模型、旧私有状态、软件安装或许可证均单独报告。一个恢复目录完整不代表引擎/科学/宿主验收通过。

新环境依当前OS/arch与仓库有效锁重建；不要复用 /private/tmp 已删除venv 或/Volumes路径。已有暂存构件dev1并非完整当前版本，正式发布从最终候选构建。原共享实例不可因恢复而被终止。

有Git时恢复有真实父提交但可能浅历史。最终bundle需明确前置对象并实际verify；不将当前浅仓库随意标成全历史独立bundle，不推送私有历史或不受信任refs。也可交付带完整文件清单的源码快照及在线恢复定义。
