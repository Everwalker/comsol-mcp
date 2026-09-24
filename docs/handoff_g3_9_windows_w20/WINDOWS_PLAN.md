# Windows双版本执行计划

1. 在新路径恢复当前固定提交。优先建立 win64 的最小公开调用链，立即用同一源码跑 win63；不要到最后才测试6.3。
2. 建立四目录：A wheel安装环境、B源码、C科学项目及公开产物、D启动cwd。私有control另设E。清除PYTHONPATH注入并记录实际模块__file__。
3. 先做真实 stdio initialize/list_tools→server连接→model绑定→单个 validate.expressions 反例。证明输入参数到达领域函数、execution/ref/revision保留、错误不丢。
4. 三入口（点号、下划线、operation_call）采用同一逻辑体，别名归一化必须在请求hash/幂等判断前完成，并拒绝重复字段矛盾。
5. 所有验证只读操作先做模型不变性测试。取实体失败不得调用任何setter；需要temporary numerical时，只创建本次临时节点且finally清理，并通过原有EVALUATE门禁。
6. 用两版本各自引擎创建基准。保存后新Worker打开同版本文件，不重新求解即可读同一存储解、验证、导出报告。
7. 每版本原始请求/响应、source、runtime、ModelRef、SolutionBinding、ObservationRef独立登记。跨版本/跨模型/过期引用作为负控。
8. 公共源受影响回归能在当前平台跑的就跑；Mac原生不可用只保留历史范围和新UNVERIFIED，不阻塞两个Windows版本。
9. 无许可/授权时只阻塞依赖步骤，留下未跑原因和可重放入口。新安全修补不能通过篡改用户Server或环境策略换来PASS。
