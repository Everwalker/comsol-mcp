# 本轮：W21 真实执行收口，继续沿用冻结标准

源码固定在 `Everwalker/comsol-mcp@c2ca5e40054fa023aafe3ae77120948063496728`。主Agent直接开发，独立Reviewer按原合同审查；不新增独立Developer，不继续给项目叠加G3.x审计轮次。

本包是**联网恢复入口＋完整本轮任务上下文**，不是完整源码离线镜像。首次运行从固定Git提交恢复源码、测试、设计文档与已提交证据；不依赖旧目录、聊天、私有分支或旧回执。COMSOL、许可证、Python/JDK和账号状态是外部前提，不在包内。

## 开始
在Windows解压至一个新的短目录，用Agent打开本包根，复制GOAL.txt。Agent应执行：
```
python tools/verify_package.py
python tools/check_contract.py
python tools/bootstrap.py
```
恢复器只取源文件，不执行仓库代码、不装依赖、不启动引擎。新目标默认repository/，已经存在则拒绝覆盖。无Git时可用`--method archive`，必须仍核对完整Git tree；归档缺失/LFS指针不等于已经恢复其外部内容。

活动任务是NEXT_GOAL.md。只按冻结W21五项检查，不重开全仓搜索新功能。frozen/保留上一工作包的16项/27目标、基准、W21五交付和阻断规则原始字节。

## 清理前
保留COMSOL 6.3/6.4安装与许可证、未保存Desktop模型、未提交/ignored的唯一科学数据。先在新目录确认源码可恢复，再清理旧代码。原文件不在GitHub就不能承诺恢复；有构建器则生成新模型和新记录，不能冒充旧SHA文件。

本次制作没有Windows/COMSOL或真实远端下载测试。包工具测试与源码机制探针的范围见PACKAGE_QA.json。
