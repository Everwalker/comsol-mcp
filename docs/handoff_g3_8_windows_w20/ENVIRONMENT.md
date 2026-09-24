# 环境与授权边界

已知用户目标：云端强模型经 Hermes/其他 MCP Host 控制 COMSOL 全流程；Windows 机器已安装 COMSOL 6.3、6.4，两者都需要适配。Mac 兼容保留，本轮以 Windows 双版本为主要真实环境。

历史报告：Windows 11 AMD64、Python 3.12.10、Temurin JDK 11。仅用于定位，当前机器仍需重新盘点；不绑定旧 IP、用户名、用户目录、PID、端口或 token。

分别发现 COMSOL_ROOT、COMSOL_JAVA_HOME、COMSOL_PROJECT_ROOT、COMSOL_SERVER_MCP_HOME。只使用当前源码真实支持的配置，不把设计字段当作已实现入口。每版本自己的 Worker/缓存/prefs/tmp/control state/结果；同一 Server API 请求串行。

程序安装目录、源码目录、科学项目目录、工作目录、控制私有目录必须分离。先核对 native executable 与 runtime build，再调用；安装目录名称不等于完整 build。不能复用 Mac 编译产物或另一个版本的 JVM。

不得为获得 PASS 注入许可、跳过 isolation、把测试服务标成 owned，或用新布尔字段自我授权。运行专用测试 Server 前核对实际监听、认证、ACL 和归属。仅操作本轮拥有的进程/模型；保留用户 Desktop 与共享 Server。

凭据不足、管理员操作需要批准、许可证缺失、无法访问某个原生平台：记录具体 BLOCKED/UNVERIFIED，停止依赖该条件的操作，继续独立工作。反复执行同一个失败命令不是进展。不要暗中改变系统级执行策略、防火墙、安装配置、全局 Python 或现有 Host 设置。
