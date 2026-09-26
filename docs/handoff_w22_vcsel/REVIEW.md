# 最新进展判断：进入W22，不重开W21

日期2026-09-25。公开基线 `2839e17738838441985ccb7a08a835bd654fe3e1`，tree `f93b4046b8556f10f003cbdc10c17bbcdead6fab`。已测试生产实现commit为 `07a44329e96d922a0955e944a8a65ba732894278`，随后提交公开证据/文档。

## 结论
**当前已有充分的范围内仓库证据支持进入W22。**这次没有发现需要阻断W22启动的新A1/A2；不是声称所有代码无缺陷。继续执行冻结范围和独立审查，只对W22引入的变化回归，不再追加W20/W21毕业门槛。

## 这次比上一提交改进在哪里
上一轮 `_g3_w21.py` 的生产操作使用演示公式和内存字典；现在已切到 `_w21_execution.py`，真实调用parameter_set/readback、study_run、result_at_points、后端ObservationRef和validate_solution，并将case/cache写入持久metadata。参数索引去掉固定8位round；未存储时间不再隐式nearest；状态传递明确设置目标Transient Study的useinitsol/initmethod/initstudy/solnum并验证初态和续算。

原W20的观测/physical状态与T11固定数组问题已在同轮专项纠正。Reviewer对两版分别做原生负控和独立重算；这是继承其仓库报告与源代码范围，不是本文作者重新执行。

## 五项已交付（仓库报告及对应代码）
|交付|6.3|6.4|依据|
|---|---|---|---|
|两参数真实瞬态扫描|4实际case，最大温差误差0.001531454K|4实际case，0.001531296K|S01/S03；S05真实执行链|
|缓存/预算|重复4 hits/0 computations；改输入最多计算预算内case|相同语义独立记录|S01/S03；S05含input_artifact hash/派发前预算|
|有限优化|9真实计算，objective约0.041169984K|9真实计算，约0.041170471K|S01/S02；独立best candidate复算|
|阶段末态→初态|initial约7.24e-6K，continuation约0.001984K|相近但不宣称逐字节相同|S01/S02；S05目标Study写读及新采样|
|同源双版本|6.3.0.290|6.4.0.293|相同候选wheel哈希与source清单、真实公共调用记录|

返回候选 k=390、rhoCp=3400000 是有限搜索中验证可行的候选，不是全局最优证明。新Reviewer在各版本上再次实际求解并重新算objective。T11记录三档实际网格DOF=375/711/1383、固定输出时刻的三档真实tolerance，独立重算12组9点场误差；28次负控拒绝numerical PASS且后续正调用恢复。S02/S03提供细节。

软件仅报告受影响范围：主开发84项Python检查、Java Worker30通过/1跳过；Reviewer独立66项。它们不是三个可直接相加的互斥全集，更不是本次完整仓库回归。Mac W21和其他平台没有因此新认证，physical仍UNVERIFIED，T047仅generic范围。

## 本次核对程度
读取S01–S09列出的源与文档范围；对照production dispatcher和case执行代码，抽查真实Windows runner里ClientSession/tools-call及数值/负控逻辑。独立Reviewer的宿主私有会话和用户机器不可访问，本文不伪称重新签署实机验收。
曾尝试读取1.5MB summary，当前连接器没有返回可用正文，因此不把该文件说成已读证据；采用可读取的machine facts、Reviewer记录和实际runner来源交叉判断。没有全仓逐行审计，没有生产运行或重跑原测试。

## 需要修改/连接的部分（属于W22，不是把W21判回失败）
1. **输入场与参数L、功率绑定**：W21支持显式input_artifacts和参数，但不会自动把L更新成新二维光斑。补W22源构建/导入/重载与source identity，同名改字节也不可复用旧结果。
2. **功率与ROI指标**：当前extract_metrics偏向point scalar；W22需要area-weighted ROI平均/std、峰谷/热点及真实热源积分。接入现有W17度量能力；不要新造统计层或用稀疏点平均替代面积。
3. **静态场/求解模式**：W21目前要求stored times，W22可以用固定空间热源+瞬态热传导；只有确需steady时才补显式Stationary路径，不伪时间也不扩大W21合同。
4. **全功率预算/不可行**：明确每颗和每环总量，非零加热目标防止零功率“最优”，区分发射/截获/吸收/边界散热。有限搜索未找到不等于数学不可行。
5. **恢复文档**：当前公开提交已含最终实现，不再对它重打旧W21 patch；private runtime/junction、凭据不可复用，科学输入必须可从包与源码生成。

## 延期事项
原计划W23光纤、W24胶形/化学/应力、W25 GUI和W26所有平台认证仍延期。真实器件发散/吸收率/温度材料与高温旋转工况需要测量/用户明确参数，本轮合成Gaussian示例不能冒充。移植这些真实模型可在W22完成后另行参数化，不拿未知材料阻止通用领域链。

## 交付与实际测试范围
本包内reference只是解析合成热流，供新环境独立开始T016/T045。用标准库生成CSV和独立矩形积分参考；没有运行COMSOL、没有新引擎结果。包恢复脚本、路径/哈希保护及reference一致性会在本机Linux容器测试。具体数目仅以PACKAGE_QA的实际输出为准。

下一Goal采用主Agent直接开发+独立Reviewer，六项W22冻结交付。通过即停止在W22，W23只列下一候选。不为这轮新增复杂签字平台，也不要求独立Developer。
