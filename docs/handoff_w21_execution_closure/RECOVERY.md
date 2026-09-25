# 空目录恢复

使用PIN.json固定的公开commit/tree。运行bootstrap.py，Git优先、HTTPS ZIP可选；原对象与逐blob校验。禁止浮动main替换、reset旧工作区、忽略不完整归档或恢复不明来源二进制。

`python tools/bootstrap.py --target NEW_DIRECTORY --method auto`
目标必须不存在；生成自己的handoff overlay和RESTORE_RECEIPT，不覆盖仓库中的旧回执。网络/体积/路径问题记录失败并保留staging，不能将部分结果当恢复完成。符号链接以惰性文本保存，Git LFS指针不含实际大文件；必要大文件需另外按LFS OID验证恢复或用原构建器生成新夹具。

Python脚本标准库可运行；Python未安装时从受信任官方源配置原生64位版本。恢复后新建venv，按实际锁/pyproject安装并pip check。构建/安装目录、数据项目根、private control、输出根分开。两个COMSOL版本独立JAR/Worker/cache/server/profile；发现实际build与JDK后再运行，不使用旧PID/token。兼容Mac不代表必须先访问Mac。

COMSOL/许可证/凭据/未提交模型不在包内。客户端有网络不代表能恢复曾经没提交的科学数据。历史私有artifact缺失明确标注，可重建模型必须创建新run，不冒充同一历史文件。

最终同步前正常fetch检查新远端差异，未经授权不push；禁止force和私有refs全量打包。归档恢复无历史时，用同PIN新Git checkout承接patch，不构造伪父提交。

本包是在旧Closure包工具上小改入口，未重写恢复框架。实际原生安装与求解仍需目标机器验证。程序安装和系统配置更改需要相应授权。
