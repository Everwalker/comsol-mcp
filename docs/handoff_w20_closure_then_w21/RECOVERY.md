# 联网恢复与现有进度

```powershell
python tools/verify_package.py
python tools/check_contract.py
python tools/bootstrap.py
```
需要Python3.10+；Windows也可用`py -3`或显式解释器。Git存在时恢复固定commit及正常分支；无Git使用`--method archive`。完整tree不匹配即失败，不追踪浮动main或静默忽略缺文件。默认全新repository/，不覆盖已存在目录。

恢复器只取源码，不运行repo、不pip install、不启动COMSOL，不改旧本地目录。副本中的旧回执保留；新回执放docs/handoff_w20_closure_then_w21/。symlink作惰性文本、LFS指针只恢复指针，缺失实数据要按回执说明获取或由recipe重建。

已完成的新代码如果在另一合法来源，先正常保存提交/补丁，再和PIN做差异比较，不回退/覆盖。没有旧文件也能按PIN重建，但不能从GitHub恢复从未提交的科学数据。发布时不得由archive模式新建无父历史强推旧仓库。

科学环境是外部前提：保留COMSOL安装/许可证、用户MPH与未保存Desktop。新建各版本的venv、Worker/control/prefs/cache和新身份，不复用旧端口/令牌/工作线程。

默认不推送远端；按已有用户授权可普通非force同步。推送前核对远端并发变化和新增公开对象中的敏感数据。此工作包本身没有修改GitHub或用户机器。
