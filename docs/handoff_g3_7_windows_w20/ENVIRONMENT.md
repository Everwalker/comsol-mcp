# 环境与权限

用户已有原生 Windows COMSOL 6.3 和 6.4。历史证据出现 6.3.0.290、6.4.0.293、Windows 11 AMD64、CPython 3.12.10；这些是历史记录，不是本轮实时发现。
不得硬编码历史用户名、IP、端口、PID、目录或当前 JDK。使用已有合法安装，发现 java/javac 的实际版本、供应商、架构和 COMSOL 运行时完整 build。
外部 JDK 11 x64 为双版本共同认证起点；其他 JDK 已经运行成功的历史证据单列，不等同官方兼容承诺，不因某目录叫 jdk11 就认定实际为 11。
每个版本隔离 preferences、Control home、数据库、端点、编译缓存、日志、models、exports、evidence。不要在同一 JVM 混装 6.3 和 6.4 JAR。
COMSOL_PROJECT_ROOT/私有目录的可信配置属于后端配置，不由工具请求体扩大。Windows 盘点脚本是只读候选发现，不是安全启动或引擎验收。

## 授权边界
允许项目内编码、测试、联网获取公开源码与正常依赖、新建任务专用运行目录和合法用户级配置。禁止修改许可证、关闭系统防火墙、默认管理员运行、全局允许远程 COMSOL 或结束用户 Desktop/共享 Server。
需要系统级安装/ACL/网络规则权限时给出最小必要理由与具体操作，等待现有授权机制；不要借此反复空转。用本任务 synthetic fixtures 做负控，不读真实 secrets。
Windows 目录权限必须基于实际 SID/ACL 回读证明；chmod(0700)、icacls 退出码或打印 passed 都不是充分证明。
本轮 native cancel 不要求凭空实现不存在/未验证的通用入口。允许能力标注 UNSUPPORTED/UNVERIFIED；强制终止只有可信独占 lease、精确进程身份与实际退出证明时可用。
