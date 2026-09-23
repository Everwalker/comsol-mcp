# 恢复流程与限制

## 固定来源

PIN.json 锁定 Everwalker/comsol-mcp 的公开 commit 与 tree。恢复脚本拒绝 main作为目标身份、意外remote及不一致URL。不是恢复私有历史，也不是从旧电脑找文件。

## Git和archive两条路径

`python tools/bootstrap.py --method auto`：有Git用固定SHA shallow fetch，新建工作分支；没有Git用固定codeload ZIP。可显式 `--method archive`。下载有预算限制，超限拒绝并保留诊断，不静默截断。

两条路径均读取所有源blob并核对完整Git tree，再核对每文件SHA256；原始链接作为惰性文本，不跟随到旧Mac路径。Windows解析前额外检查保留设备名、ADS、尾点/空格和大小写别名碰撞。不能自动重命名源文件来假造tree通过；若公开源含Windows不可落地路径，停止原目录恢复并提出可追踪的物化/历史证据分离方案，不把部分源称完整恢复。

默认恢复到 repository/，已存在则拒绝。Git路由只抓公开祖先，禁止引入曾包含token的私有分支。没有Git的archive源可以先开发；最终需要Git时从同PIN建立新git工作树，核对后移植改动，绝不把当前工作覆盖掉。

## 新回执

恢复后：`repository/docs/handoff_g3_6_windows/RESTORE_RECEIPT.json`，包含commit/tree、文件列表、哈希和恢复方法。本包说明复制到同目录。仓库根已有旧RESTORE_RECEIPT.json保持历史原状。

`tools/audit_repository.py` 再读取核对所有恢复文件，并做Python/JSON解析和历史路径候选扫描。**机器读取/解析不是逐行语义审查，不是测试通过或COMSOL验收**。

## 外部依赖

依赖从网络合法下载到新venv；COMSOL、JDK、Python的实际版本必须盘点验证。本包不含这些软件或许可证。外部云模型凭据由Host自身安全配置提供，不写进证据。无法恢复未提交/ignored/未保存模型；已提交构建器可以产生新夹具，但新文件不是历史同SHA产物。

## 保证范围

本次制作环境不能取得整个仓库归档，故包内不冒充含源。恢复算法和包文件会在本地测试；真实GitHub恢复、Windows API、COMSOL6.3/6.4与Host需由目标机完成。PACKAGE_QA.json准确记录已执行和未执行项。
