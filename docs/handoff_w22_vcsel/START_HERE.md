# W22 VCSEL 续开发包

**阶段结论：W21 已有范围明确的真实双版本批准；本轮进入 W22。**不再开启新的 G3.x 全局收口轮次。

此 ZIP 是联网恢复型工作包，不是完整源码离线镜像。首次运行从公开固定 commit `2839e17738838441985ccb7a08a835bd654fe3e1` 恢复所有 Git 跟踪内容并校验 root tree `f93b4046b8556f10f003cbdc10c17bbcdead6fab`。制作环境无法下载仓库归档，因此未内嵌完整源码或声称远端恢复已实跑。

在 Windows 新目录解压，让 Agent 打开整个包根目录，粘贴 GOAL.txt。所有任务背景、规则、参考输入及恢复程序都在包内，不依赖旧聊天/旧目录。

```
python tools/verify_package.py
python -m unittest discover -s tests -v
python tools/bootstrap.py
```
有 Git 优先使用 Git；无 Git 可 `--method archive`，但归档缺少跟踪对象或树哈希不符会拒绝。Python 是外部前提，使用可信的原生64位解释器。COMSOL6.3/6.4、JDK与许可证不是本包附赠物。

恢复后本轮材料在 `repository/docs/handoff_w22_vcsel/`，原仓库历史说明不删除。正常开发从当前完整提交开始，**无需再次应用旧 `w21_final_code.patch`**；它只用于从旧 c2ca5e4 基线恢复历史。

`reference/` 内是合成参数与解析函数生成的二维热流测试输入，可供新任务独立重建；不是你的真实 VCSEL 发散、吸收率或晶圆材料数据。它没有经过 COMSOL 求解。

保留软件安装、许可证、Agent登录、唯一未提交科学数据和未保存模型。建议新目录恢复成功后再清理旧代码；GitHub不能恢复从未提交的文件。
