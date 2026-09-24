# 干净目录恢复

## 范围
固定提交中的源码、测试、设计文档、已提交证据可以恢复。依赖环境、Worker编译缓存和本机帮助索引要重建；COMSOL、许可证、凭据、未提交MPH、未保存Desktop模型和外部实验数据不在包中。
新建测试模型不等于重建历史同SHA文件；没有原文件时记录不可获得，而不是改历史哈希。

## 命令
```
python tools/verify_package.py
python tools/bootstrap.py --method auto
python tools/audit_repository.py --help
```
优先Git按commit恢复、核对root tree并按Git对象原字节写文件；不需要安装Git时可使用HTTPS archive分支。
```
python tools/bootstrap.py --method archive
```
不自动退回main。目标目录必须不存在；失败的staging保留以供诊断，不能继续把它当完整仓库。
恢复默认 repository/，历史 RESTORE_RECEIPT.json 不改；本轮回执进入 docs/handoff_g3_9_windows_w20/。
源码核对仅用于新恢复状态，开发后有意修改不能继续声称零差异，应生成基线-新源码diff与新的清单。

## 安全和版本控制
脚本不执行仓库代码、不装依赖、不启动COMSOL。归档路径、Windows大小写别名、设备名及越界均检查；symlink按惰性文本保留，不自动激活。
归档模式没有原Git历史；需要提交时安装Git并获取同一固定提交，再准确迁移变更，不能把archive init成一个伪造父提交。
如果恢复体积超过固定预算，先检查上游规模，再显式调整；不要跳过校验或下载main。

## 交付后复现
包含新的源码身份、实际运行源哈希和与发布源的对应。授权上传前检查来源、许可证和敏感信息；未获推送授权则生成diff/bundle与可复现命令，不标远端已同步。
