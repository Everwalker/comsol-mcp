# G3.4 验收表

所有下列新增验收初始为 **NOT_RUN**。这是下一 Agent 的测试规格，不是本次成绩单。原 G3.3 native 原始记录保留，不因本表重置历史。`required=true` 表示本轮声明相应范围通过必须有证据；没有环境仍应 BLOCKED，不能当 PASS。软件缺陷导致未执行时标实现未完成，不能假称外部环境限制。

每个 case 的最低证据：source commit/tree+文件manifest、环境、请求/响应、执行命令/exit code、断言、evidence_level、产物身份；真实引擎追加日志。图片追加 pixel/MIME/hash与数据绑定；发布路径和秘密信息分离。

| ID | 范围 | 动作和判据 |
|---|---|---|
| A01 | SOURCE | 固定公开 PIN 从不存在的新目录恢复；逐文件 Git blob/tree 校验；原历史回执不覆盖；坏 archive/hash/路径逃逸拒绝。 |
| A02 | INSTALL+PROTOCOL+NATIVE | 安装路径 A、源码 B、项目 C、cwd D；project root 来自可信配置；导出/模型保存只在 C，A/B/D无科学输出。源外 wheel 不是仅 import。 |
| A03 | SECURITY+PROTOCOL | 使用合成 sentinel 位于私有控制、.env、历史 private 子树；artifact ID/path 不得读取这些内容；已注册允许产物可读；跨项目、symlink、文件替换拒绝。不要读真实凭据。 |
| A04 | DATA | 2表达式×2非连续outer×2inner×多点复场，JSON→CSV→重建；labels/时间/坐标/单位/实虚部一一对应，字段乱序不影响；未知绑定明确拒绝。 |
| A05 | PROTOCOL | 强制 storage=artifact 与自动阈值；wire 无完整 values/data/图像重复，只有有界 preview；artifact 有完整轴/单位/provenance；job_result 与 text mirror不重新膨胀。 |
| A06 | ARTIFACT | 至少多块结果；offset/length/hash正确；同文件稳定身份，替换/截短拒绝；测读取字节数并报告当前重复全文件hash成本，不伪称线性。 |
| A07 | EVIDENCE | 公开 SHA/tree、runtime/publication source bridge、八组 evidence 指向可访问来源；旧 pending 与新 verdict 区分；mock/CONTROL不变native，未公开本地历史不依赖。 |
| V01 | CONTRACT | W18每一已发布动作有schema/effect/域/真实返回契约；旧全量/领域/专家profiles和fallback可用；注册定义数不当实际支持数。 |
| V02 | NATIVE | 正确创建/修改/回读/移除1D/2D/3D PlotGroup与feature；修改目标同时保留用户其他Results/Derived Values/Table/solver；错误type/路径有明确异常。 |
| V03 | NATIVE_RENDER | 稳态非均匀解析或既有可信基准：3D Surface及1D曲线由COMSOL输出；数据集/解/单位/图例/范围可回读，对照 W17 数值。有效PNG/MIME/尺寸，非旧缓存/空白。 |
| V04 | NATIVE_RENDER | CutPlane上的2D图与几何图；渲染位置/坐标系/法向正确，图类型不串；几何图不标为场图。离轴热点不改径向平均。 |
| V05 | NATIVE_DATA_BINDING | 两个不同非初始时间或outer参数，读实际SolutionInfo和对应数值后分别渲染；选择正确。参数改后未solve时明确STALE_SOLUTION或拒绝，不出“新结果”。 |
| V06 | NEGATIVE | 错dataset/solution/expr/unit/不存在的plot、渲染异常、假PNG、上次缓存图、错误format、未批准覆盖，不发布成功新artifact；原有完整文件保持。 |
| V07 | MCP_IMAGE | 实际stdio返回ImageContent/实际可取资源；解码hash与本次COMSOL图一致；structured/text仅bounded metadata；损坏图/过量像素/大base64拒绝。 |
| V08 | HOST | 至少一个实际可访问Host收到图；记录host版本与回传路径。Hermes未测只标未测；模拟client不称Hermes认证。无图Host有文本/引用降级和truthful vision status。 |
| V09 | REOPEN | 保存图形设置与stored solution；新Worker打开同一SHA文件，不先solve；回读plot/view/export配置，重渲染并数值核对。旧ref失效，不以“文件存在”通过。 |
| V10 | JOB+SAFETY | 长render/export期间health/job可响应；断开Host后同key获取原job，无重复导出；清理失败危险标志保留；只处理自有资源。共享Server未终止。 |
| V11 | DELIVERY | 全量软件与受影响native回归、source-outside安装、图形实际支持矩阵、secret scan本轮Git对象、正常非强制同步和新目录恢复通过，结束于W18。 |

## 判据补充

A03 的“路径合格”必须涵盖文件用途/发布授权，而非只通过 resolve_relative_to。A04 点/时间/outer标签不得用数组位置替代。A05 返回budget统计最终JSON/图像/文本多重复制，不只引擎数组。V05 PNG差异不独自证明科学正确。V07 仅输出 `file:///...png` 路径不等于云端模型拿到了图片。V08 native截图和模型视觉理解区分：可证明实际收到图像，不承诺任意顶级模型的判断总正确。

可将V03/V04使用同一小模型减少耗时，但故意失败模型与主正常模型隔离。质量测试阈值在运行前登记，不为过关事后放宽。保留当前部分BLOCKED/NOT_RUN子项；对本轮不依赖项说明理由，不删除定义。

## W18 完成状态

- `W18_API_VISUAL_VERIFIED_SCOPED`：仅当上述本机 API 图形、数据绑定、传输、重开、负控与回归通过。
- `HOST_DELIVERY_UNVERIFIED` 可作为独立附加状态，但此时不能说“云端Hermes视觉闭环已完成”。
- `IMPLEMENTED_WITH_BLOCKED_NATIVE_ACCEPTANCE`：代码/协议做好但缺图形环境或授权。
- 其他平台/版本、GUI共享绑定、取消、动画/报告未测能力单列，不由本机PNG测试升级。
