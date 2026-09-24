# G3.7 审查 → G3.8 决策

**结论：保留双版本引擎与既有基础；暂不进入 W21。先把 W20 从“可调用框架+数学比较”修成可追溯的真实模型验证系统。**

## 1. 基线与本次边界
审查日期 2026-09-24；固定公开提交 `59d741d6e2514925fcabe3eb8fa7a1661e309779`，tree `1d60b1fc4d7d5a016dac1bf6a1bff2bb882384f0`。该提交报告了新 Windows 双版本运行和 W20 完成。

本次依据 connector 获取的提交/树、SOURCE_MAP.json 中的源码范围、报告生成器、部分测试和台账。未连接用户 Windows 或 COMSOL，未重跑全库pytest，未取得完整二进制源码归档；不声称逐行审完仓库。13项本地探针基于取回源代码摘录的函数体，在无COMSOL/ACL模拟环境运行。恢复后可做AST一致性比较；本次未在完整本地仓库做此比较。控制流复现不等于生产系统事故或已被利用漏洞。

## 2. 实际进度与应保留的东西
- 最新Windows记录运行ID为 `g3_6_acceptance_20260924T064900Z`；记录15 CONTROL_PASS、14 NATIVE_PASS_SCOPED、1 SOFTWARE_PASS，总30；这是旧G3.6驱动的新运行，不是专用W20原生驱动。
- handoff进度报告1918 passed/8 skipped，含数学helper与Gate A测试；本次没有重新运行，不能作为本次测试数。
- 晚到结果的 `_finish()` 开始采用持久化权威结果；SID查询和icacls退出码检查、Windows查询错误不填虚构command、JAR内容参与缓存等都有实现或进度改进。按实际代码保留，不重写整个基础层。
- 8个validate.*已接入模块注册，但下面的问题使它们的“已实现”不能等同“完整验证”。

## 3. 关键证据问题（必须先纠正）
`tools/generate_w20_evidence.py` 中稳态观察直接设置为312.502/325.001/337.498和80.05；瞬态观察明确由 `exact + 0.002*cos(x+t)` 生成；收敛误差0.0452/0.0118/0.0031、耗时和内存是固定列表。随后同一组数据用于两个版本的报告。

`tools/generate_g3_7_report.py` 并未因此运行 COMSOL。它在循环里直接写 `status=PASS`、`production_entrypoint=True`、`evidence_level=req_ev`，并以这些文件/旧运行结果为证据。例如B03/B04/B05直接复写上面的数字。文件确实存在且哈希正确，并不能把合成数据变成实机证据。

这不否认已有稳态模型/图像/MPH的原生运行；需要纠正的是W20各项认证范围，不能无依据指认人的动机。新报告只可由实际运行日志生成；旧文件保留并新增更正索引。报告schema/hash检查只做结构审计，不证明原生运行。

## 4. 验证语义缺口
### 4.1 无输入/无解可能通过
`validate.solution()` 不使用worker读取解；`passed=True`起步，只检查传来的criteria。空输入、未知oracle、只提供4个必需点中的1个都能返回PASS。`validate.conservation()` 默认in/out=0、normalization=1，把“没有测量”变成了完美平衡。探针P01–P03/P07复现。

### 4.2 部分规则还没有实际检查
`validate.expressions()` 对字符串列表不做求值却报检查完成；非空坏表达式字典也可能PASS。`validate.boundary_conditions()` 给每个rule名加PASS，不读边界节点。`validate.structure()` 的unsupported_reasons没有进入最终裁决；preflight忽略UNVERIFIED，未真正检查材料/选区却在结果中列为已检查。探针P04–P06，加源码控制流检查支持。

### 4.3 基准与收敛不可靠
Oracle的set方法冻结了，但公开expectations字典仍可改写。validate_convergence忽略criteria，3次相同mesh size、误差100→90→80也通过。递减趋势和满足精度不是同一件事；本轮应要求实际精度变更、执行证据和冻结指标，不要用恰好是线性精确场强迫递减曲线。探针P08–P09复现。

### 4.4 报告生成不能升级结论
validate.report无论输入检查结果是什么都返回numerical PASS；默认把runtime写成6.4.0.293，证据hash包含当前时间和输入文本，不对应证据文件链；destination写失败被吞掉。P10用临时目录当文件目标，验证失败仍报PASS。当前只确认handler行为，公共入口的越界暴露须进一步追踪；修复应复用ArtifactStore而不是仅依赖上游可能的检查。

## 5. Windows ACL仍有一个具体误判
新版确实查询了SID、检查了命令退出码，但将首行整段路径和主体一起当ace_identity；`short_user in ace_identity` 可被目录用户名命中。用户名子串也可以命中另一个主体，零个ACE解析成功时没有拒绝。P11–P13仅使用模拟icacls输出，未修改真实ACL。应比较实际安全描述符中的trustee SID和mask，不能靠英文名称子串；也不能将NULL/空DACL混为一谈。

## 6. 下一轮怎么避免又只做出一份全PASS报告
1. 先建立错误模型的负控：无解、坏表达式、缺观测、未知getter、错误边界，必须让现有产品validator识别。
2. 只读validator从真实ModelRef读取，不自动修改或求解；数学比较模式与模型验证模式明确分离。
3. 真实数值经W17受控读取，并记录dataset/solution/outer/inner/坐标/单位。期望可来自caller，但观测不能由caller冒充引擎。
4. 冻结独立基准，两个版本分别建模、取数；先做小链，再做三精度级别和报告。测试PASS可以对应validation FAIL的负控。
5. production entrypoint必须有真正stdio请求响应；直接域函数调用只算helper/native API级，不能填标志冒充。
6. 先完成W20再进入W21。扫描优化会自动挑选“通过”的结果，必须先保证通过具有意义。

## 7. 交付说明
本包是固定公开提交联网恢复入口，不含完整源码归档。bootstrap校验Git tree/逐文件hash；无旧目录依赖。不能恢复未提交模型、Desktop未保存内容、许可证和私有凭据。原测试构建器可生成新模型和新记录，不能冒充旧SHA历史。

附带探针结果属于审查诊断，不能记为项目原生验收PASS。本包的结构审计器不可能仅凭日志文件加哈希证明Windows/COMSOL实际运行；必须结合运行器代码与原始证据审阅。

## 8. 来源
具体定位和blob在SOURCE_MAP.json。外部核实仅用于建议，不替代仓库事实：COMSOL官方mesh refinement说明需要更细网格重新求解比较；Microsoft文档提供安全描述符/ACL/SID的机器可读接口。本轮数值夹具与修复计划属于设计建议，不是现有软件能力。
