# 从干净目录恢复

## 固定来源
- Repository: Everwalker/comsol-mcp
- Commit: `59d741d6e2514925fcabe3eb8fa7a1661e309779`
- Tree: `1d60b1fc4d7d5a016dac1bf6a1bff2bb882384f0`
- 模式：ONLINE_PINNED_SOURCE_RECOVERY；本包没有携带完整仓库归档。

`python tools/bootstrap.py` 默认使用 Git；没有 Git 时使用 HTTPS ZIP。两个路径均在落盘发布前验证固定 tree。Git 路径禁用 hooks、LFS smudge、自动换行转换；只接受固定公开远端。恢复不执行仓库代码。符号链接以惰性文本恢复，submodule 拒绝而不是静默跳过。

目标默认 `repository/`。目标已有内容时拒绝覆盖，不能删除它以掩盖用户修改。需要另选新路径可用 `--destination PATH`。网络失败后保留失败回执，说明原因；不降级到 main、不跳过校验。归档若因 LFS/export-ignore 等不能重建同一 tree，改用受验证的 Git 路径，不自行拼一个近似快照。

恢复完成会在 `docs/handoff_g3_8_windows_w20/` 写入本次文档与回执；旧历史回执保持不变。包含了源码树逐文件大小、Git blob 和 SHA256。若使用归档，随后同步 Git 时必须先取回同一公开 commit 并校验，把自己的改动以正常补丁/提交应用；不能创建一个伪造的“历史原提交”。

## 运行前提
Python 3.10+ 可执行恢复；项目运行优先复用已验证的原生 Windows Python 3.12 x64 版本族，新建 venv。COMSOL 6.3/6.4 和外部 JDK 11 x64 独立盘点，不混用 JAR。缺失软件由 Agent 根据官方来源处理，记录来源/版本/hash；本恢复脚本不自动安装。

## 重要限制
当前交付作者环境无法取得远端二进制归档（运行环境 DNS/归档下载不可用），所以没有执行完整远端恢复；已经通过 connector 阅读固定来源文本并在本地验证交接工具。不得把本包本地测试当作 Windows 或 COMSOL 通过。

本包工具的归档预算为压缩 512 MiB / 解压 2 GiB / 30000 项。若仓库未来超限，先盘点并合理扩容或使用 Git 路径；不关闭路径、tree、来源检查。恢复固定 commit 不会随未来 main 自动变化。

## 同步
允许在任务分支修补、测试、生成脱敏证据后正常同步到已授权仓库。缺少凭据时提交本地 git bundle/patch 和校验回执；不 force push、不清理用户原始数据、不在包内放 token。下一次交付可优先加固定源码快照，但先确认敏感数据和第三方文件授权。
