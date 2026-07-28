# B05 无喷气与 JET_01 升力反常诊断

## 诊断口径

- `G01_JET01_existing` 没有喷气前基准阶段，因此本报告不能做同一 case 的喷气前/喷气后对比。
- 当前采用 `G00_nojet_existing` 的同时间窗口作为跨 case 基准，并按样本序号/物理时间对齐。
- 当前 CLI 宏口径为先写入 row i 的质量流量，再推进到 `t_end` 并采样，因此 `t_end` 样本归属于刚结束的 row i。
- `J01..J24` 是喷气口，`JET01..JET24` 是底部区域；单喷气时 `J01` 为质量流量入口，no-jet 时 `J` 系列为壁面。
- 质量流量入口正值 `2.57 kg/s` 已确认表示喷入计算域；当前 STAR 模板没有 `actual_massflow_01` 回读 monitor。

## 已确认的力定义

- 6 个区域力、`Fz` 和 `Jet_Reaction_Z` 均为 `Laboratory [0,0,1]` 方向、单位 `N`、压力+剪切力。
- 6 个区域力互斥覆盖 `JET01..JET24` 底部区域。
- `Fz` 覆盖 `JET01..JET24` 底部边界；建模人已确认它实际不包含 `tail`。
- `Jet_Reaction_Z` 覆盖 `J01..J24` 喷气口，与 `Fz` 的 `JET` 底部区域不重叠。
- `Fz_Total + Jet_Reaction_Z` 可作为当前 report 覆盖表面的候选有效 +Z 力，但 `Fz_Total` 与 6 区底部力之和的差异仍需解释。

## 主要结果

- 按当前阈值法，最早发生明显力/力矩偏离的是 `Roll_Moment`，时间约 `t=0.0013 s`，差值 `1772.21`。
- 6 个区域中平均变化最大的是 `Fz_S1R`，相对 no-jet 同时间窗口平均变化 `1215 N`。
- 6 区底部力之和平均变化 `1875.32 N`。
- `Fz_Total` 平均变化 `4441.04 N`。
- `Jet_Reaction_Z` 平均变化 `461.936 N`。
- 候选有效 +Z 力 `Fz_Total + Jet_Reaction_Z` 平均变化 `4902.98 N`。
- `actual_massflow_01` 缺失，因此无法画真实质量流量曲线；图中只给出边界条件设定的 `cmd_massflow_01=2.57 kg/s`。

## 对异常的当前解释

- 已确认原因：`Jet_Reaction_Z` 无喷气非零不是 CSV 读取错误；它是喷气口边界上的 +Z 压力+剪切力 report，即使 no-jet 时 `J` 为壁面也可能有压力/剪切贡献。
- 已确认修正：建模人确认 `Fz` 实际不包含 `tail`；因此 `Fz_Total` 与 6 区底部力之和的差异不能再用 `tail` 解释，需要继续核对积分面或导出映射。
- 最可能原因：单喷气使底部区域力发生真实响应，其中 `S1R` 平均变化最大；整体 `Fz_Total` 和候选有效 +Z 力均为正向变化。
- 缺少证据：没有 `actual_massflow_01` monitor，无法独立验证实际质量流量曲线；没有单喷气前基准段，只能使用 no-jet 同时间窗口作为跨 case 基准；checkpoint 哈希未写入 CSV。
- 待物理判断：如果后续流场证据显示局部区域存在反向响应，仍需判断喷气是否破坏局部高压区、增强泄漏、产生卷吸或改变底部压力恢复。

## 输出文件

- `artifacts/reports/B05_abnormal_lift_diagnosis.csv`
- `artifacts/reports/figures/aligned_force_comparison.png`
- `artifacts/reports/figures/aero_reaction_effective_force.png`
- `artifacts/reports/figures/massflow_and_force.png`

## 数值摘要

| 量 | no-jet 均值 | JET_01 均值 | 平均变化 | 首个阈值时刻 s | 备注 |
| --- | ---: | ---: | ---: | ---: | --- |
| `Fz_S1L` | 5876.598406 | 6501.968516 | 625.3701107 | NA |  |
| `Fz_S1R` | 5868.14855 | 7083.151826 | 1215.003275 | NA |  |
| `Fz_S2L` | -7749.323244 | -8088.365678 | -339.0424343 | NA |  |
| `Fz_S2R` | -7722.80455 | -7779.968217 | -57.16366722 | NA |  |
| `Fz_S3L` | -11330.82249 | -11091.37854 | 239.4439442 | NA |  |
| `Fz_S3R` | -11286.51457 | -11094.8041 | 191.7104672 | NA |  |
| `regional_lift_sum` | -26344.7179 | -24469.3962 | 1875.321696 | NA | sum of six bottom-region reports; Fz excludes tail but still differs, mapping/surface definition needs follow-up |
| `Fz_Total` | 52844.71694 | 57285.76085 | 4441.043904 | NA |  |
| `Drag_Total` | 33838.01843 | 33511.83799 | -326.1804424 | NA |  |
| `Pitch_Moment` | -495426.6994 | -505018.1697 | -9591.470272 | NA |  |
| `Roll_Moment` | -58.73885984 | 78.82355578 | 137.5624156 | 0.0013 |  |
| `Jet_Reaction_Z` | -330.2221855 | 131.7142918 | 461.9364773 | 0.0065 |  |
| `effective_Fz_plus_reaction` | 52514.49476 | 57417.47514 | 4902.980381 | NA | candidate sum because Fz surfaces and Jet_Reaction_Z surfaces are non-overlapping in current report definitions; not strict whole-vehicle lift |
| `cmd_massflow_01` | 0 | 2.57 | 2.57 | 0.0001 | commanded massflow; positive value confirmed as into computational domain |
| `actual_massflow_01` | NA | NA | NA | NA | missing_actual_massflow_monitor; boundary condition only, no independent STAR monitor |
