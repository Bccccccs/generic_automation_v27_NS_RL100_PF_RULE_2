# B04 Physics QC Explanation

本检查是在原有数据体检基础上增加的物理接口一致性检查。原有检查回答“CSV 是否能被可靠读取”，B04 回答“读出来的数据和 case manifest、喷气边界、动作窗口、质量流量、力核算之间有没有明显矛盾”。它仍然不能证明 CFD 物理正确，只能发现接口层面的高风险问题。

## 结果分类

### 1. 格式错误

能发现：
- `physical_time` 缺失、非数值、非严格单调。
- `timeseries.csv` 和 `actuation_schedule.csv` 行数不一致。
- STAR 原始 CSV 与标准 `timeseries.csv` 的采样时间不能对齐。
- 喷气开始/结束对应的动作窗口不覆盖 STAR 采样时刻。

不能证明：
- 时间对齐规则一定就是 STAR 宏真实的边界切换规则。当前样例很多采样落在窗口终点，需要浩坤确认窗口归属。

### 2. 名称或坐标错误

能发现：
- 24 个统一喷气编号缺失或重复。
- 多个统一编号映射到同一个 STAR Boundary。
- `Fz_Total`、`Drag_Total`、`Pitch_Moment`、`Roll_Moment` 的 STAR report 方向向量与 `case_manifest.yaml` 不一致。
- 列名看起来像升力、阻力或力矩，但 report mapping 中方向向量矛盾。

不能证明：
- 只凭 `Fz`、`Drag`、`Pitch`、`Roll` 这些列名证明真实方向正确。必须有 STAR report 的 Coordinate System、Direction Vector、Moment Center 和积分表面。

### 3. 质量流量错误

能发现：
- JET 关闭时 `actual_massflow_NN` 非零。
- JET 打开时缺少 `actual_massflow_NN`。
- 缺少 `actual_massflow_NN` 时明确报警，并且不会用 `cmd_massflow_NN` 自动代替。
- 按喷气编号保存 `cmd_massflow_NN - actual_massflow_NN` 的最大绝对误差和平均绝对误差。
- 无喷气算例里喷气开关、喷气质量流量或 `Jet_Reaction_Z` 非零。

不能证明：
- 质量流量误差小就说明喷气边界面积、法向、湍流响应或喷口模型正确。

### 4. 力核算错误

能发现：
- 六个承载区域力、整车气动力/力矩或喷气反作用力序列缺失。
- 报告会分别保存 `regional_lift_sum`、`vehicle_aerodynamics` 和 `jet_reaction` 的统计量。

不能证明：
- 不能直接假设 `Fz_Total == 六个区域力之和`。两者可能来自不同 STAR report、不同积分表面或不同方向定义。若二者差异明显，报告进入“需要浩坤判断的物理问题”。

### 5. 数值不稳定警告

能发现：
- 力或力矩序列动态范围异常大，提示可能有启动瞬态、采样异常或求解发散迹象。

不能证明：
- 不能仅凭该警告判断仿真一定发散，也不能仅凭没有警告判断流场稳定。

### 6. 需要浩坤判断的物理问题

能记录：
- 喷气 Boundary 的面积、法向或真实 Region 尚未确认。
- report 的 Direction Vector、Coordinate System、积分表面、力矩中心尚未确认。
- `Fz_Total` 与六个区域力之和明显不同，但当前信息不足以判定谁错。

不能证明：
- 这些项不是程序可凭列名自动补全的问题。未确认时必须保留为待判断，不能默默填 0，也不能把“CSV 没有 NaN”写成“CFD 物理正确”。

## 当前样例报告说明

`B04_physics_QC_report.json` 对两个当前真实样例运行：

- `runs/temp_j01_3s_mass2p57_dt1e-4`
- `runs/temp_no_jet_3s_dt1e-4`

当前报告中，无喷气样例的 `Jet_Reaction_Z` 存在明显非零值，因此被归入质量流量/喷气反作用力错误。这不是 NaN 或缺列问题，而是物理接口定义需要核实的问题：无喷气 case 中该列应为 0 或明确不适用。
