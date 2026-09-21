# G3.3 必做验收（全为待执行定义）

本表定义新的测试，不是测试结果。先登记真实函数/来源，再冻结预期和误差标准。
软件/协议/引擎/数值/物理证据分层；不得用只检查返回标志的测试替代数学值断言。

| ID | 范围/原T映射 | 输入与必须验证的结果 |
|---|---|---|
| C00 | 清洁恢复 | 空目录恢复pin源码；commit/tree/blob一致；不用旧路径；新env测试/wheel源外import；已提交文件总数由实际清单生成。 |
| C01 | 证据更正 | phase4_1/4_2/w17历史hash保留；Fake测试只能CONTROL_UNIT；所有当前PASS都有正确证据等级与真实引用；缺audit文件标MISSING。 |
| C02 | T033/T038危险状态 | 各层false/true/null组合、异常前后派发、清理失败、未知结果与export；MCP/job/ledger不允许成功掩盖危险。 |
| C03 | Gate A/F02重开 | A/B/C保存前实际场值，严格同hash新Worker直接读存储解，不求解；负控走同一检查器。 |
| C04 | T013统计 | f=2,V=3：integral=6、average=2、std=0、RMS=2；分母必须来自V=3，不是1。 |
| C05 | T013多维/selection | 1D端点/线、2D边界/面、3D面/体；不同comp/geom；部分选区与全域不同结果；不固定geom1。 |
| C06 | T013非均匀场 | f=x+2y，矩形[0,2]×[0,3]：面积6、积分24、平均4、方差10/3、std=sqrt(10/3)、RMS=sqrt(58/3)。用独立解析式，不用模型回读生成期待值。 |
| C07 | T013轴对称 | 圆柱R=2,H=3：积分1得12π、平均r得4/3；侧面积12π；证明确实采用物理旋转测度且权重一次，不能只检查applied_count。 |
| C08 | T021解轴 | >=2表达式×>=2outer×>=3inner×>=3点，值显式依赖四个索引；检查all/子集/first/last以及0、越界、错dataset/solution；time与param元数据来自实际解。 |
| C09 | T014复数 | 3+4i和空间变化复场：preserve/real/imag/abs/phase一致；虚部缺失、getter失败、shape错必须失败；确认实数场才允许imag=0。 |
| C10 | T015点坐标 | 同物理点以m/mm请求结果一致；错误回读点、维数/排序/shape/非finite失败；不支持frame拒绝；回读非空不自动VERIFIED。 |
| C11 | Dataset/Nodes | Solution→CutPlane/CutLine/CutPoint、有效Join；图环/错组件/同tag不同collection拒绝；中途property失败停止后续、真实回读。 |
| C12 | T035/T030导出 | 项目外/parent/symlink/已有目标保护；拒绝须在文件副作用之前；eval或cleanup失败不能发布有效结果；原子失败保留原文件hash。只用任务临时哨兵文件。 |
| C13 | T049大数组 | 真正artifact/chunk请求可按offset/length重构完整字节和shape；streaming hash与固定hash一致；峰值内存记录；损坏块/越界/错权限拒绝；不能全量read_bytes后假装分页。 |
| C14 | Probe/Table | Definitions Probe与Derived Values分开；用户表/表达式/选择/复数数据保留；新表写后回读；不以echo的输入作为回读。 |
| C15 | T010/T012/T027 | 结果求值同key重试一次；新观察新key；求解期间控制面响应；未知任务不重放；恢复后旧引用失效；不停止共享Server。 |
| C16 | 打包与schema | Python直接依赖声明/机器锁；源外wheel包含所有operation/schema/Java资源；新的MCP input/output schema与真实行为一致。 |
| C17 | 收尾/发布 | 新源hash绑定所有报告；只有已授权自有进程清理；本轮配置修改按授权恢复；普通同步核对SHA；未验证矩阵保持未验证。 |

## 数值容差

必须在运行前固定容差，结合解析场与求解设置给出理由。不能看失败后放宽阈值。
解析场直接评估与PDE离散解要区分；常量场、算术变换使用接近浮点误差的合理界限，
PDE解使用网格/时间收敛与原G3约定的容差。保存前后对比同时记录绝对/相对误差。

## 复数统计与轴顺序

默认保留复数。最值/排序对复数必须声明比较量或拒绝；phase在0幅值的含义显式。
FieldArray标出所有轴及长度；切片不能顺带删除未选择轴而不更新schema。
两表达式三时间的data[1]是第二表达式，不是第二时间。跨outer的stored time可能不同，
不准用第一outer的时间表给所有case贴标签。

## 实机/无实机边界

FakeEngine可验证错误契约、调用参数和负控检查器，不提供真实性能/物理结果。
若某商业模块缺许可，可保留真实失败与BLOCKED并完成核心；不能把代码拒绝误判成缺许可。
当前目标是Mac可用范围，Windows/6.3/Intel/GUI另列UNVERIFIED，不在本Goal认证。
