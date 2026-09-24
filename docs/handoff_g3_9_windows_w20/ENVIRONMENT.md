# 当前用户环境和部署约束

用户已明确：Windows安装了COMSOL 6.3与6.4，两者都需适配；保持联网；可能清空旧本地项目。Windows是本阶段主要原生执行环境，Mac兼容需要保留，但不可在无Mac环境时声称新Mac原生回归。
不得要求先建Mac↔Windows SSH或把Mac设为唯一集成端。Windows Agent可以修改公共代码。

历史候选目录（只用于发现，必须重新核实）：
- C:\Program Files\COMSOL\COMSOL63\Multiphysics
- C:\Program Files\COMSOL\COMSOL64\Multiphysics
- 对应 bin\win64\comsolmphserver.exe 与 bin\comsolclientpath.txt
历史Python为3.12 x64；建议以新3.12 x64 venv测试，不强迫更换已有合适解释器。
外部JDK11 x64优先，重新读取java/javac真实版本、vendor、架构及官方classpath，不能根据变量名jdk11或COMSOL自带JRE猜测。
过去记录中的build（包含.290/.293）不当作本机现值。本轮逐个引擎实际读版本与build。

每版本独立项目产物根、私有control root、Server prefs/tmp/recovery、Worker state/cache和文档索引版本。共享源代码。
普通MCP入口需要验证当前隔离与所有权策略；直接Worker成功不允许替代这个证明。
不得停止未知/共享Server、用户Desktop和其他未保存模型，不修改防火墙/VPN/网卡，不读取或复制登录token。
原生取消不属于本轮新增目标；已有能力如不满足所有权/退出确认，明确拒绝并保留限制，不能为验证通过放宽权限。
