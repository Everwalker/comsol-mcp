# 环境和支持范围
双Windows原生仍需COMSOL6.3/6.4，保留macOS ARM6.4；Intel构件与已声明硬件跳过按config/TARGETS处理。版本/build/架构从当前真实进程与安装发现，不沿用旧字符串。
使用新的原生Python环境与匹配的external Java工具链。按官方client/server接口支持范围及已验证版本选JDK（11 x64/arm为现有候选，不因COMSOL内部JVM版本推断外部要求）；实际java/javac、classpath manifest和JAR内容写入环境记录。
每个Runtime独立进程、编译缓存/endpoint/control DB/prefs/recovery；生产包资源、科学项目根和私有控制根分离。同一Server模型API串行，控制查询不能排队等求解。
任务授权应足够完成批量操作，但不扩大为OS无限权限。普通新case不需要逐setter确认；私有文件、共享Server删除、全局网络/GUI权限仍要已有授权。测试只使用本任务新目录与合成模型。
不存在的许可证/机器/GUI权限必须明确BLOCKED，不换标签称PASS；可做的软件、build和另一平台工作继续。云端Host没有视觉授权可先给协议输出证据，但不要声称模型已经看图。
