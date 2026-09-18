# B07 当前阻塞问题

## 结论

当前最小数据接力链路已经跑通：浩坤给出的 STAR 导出数据可以被博超侧读取、合并成 `timeseries.csv`，并生成 `quality_report.json` 和四张自动图。

当前没有阻塞格式读取的问题。剩余问题主要是接口口径和物理解释问题，需要两人联合确认。

## 格式问题

- 已通过：两个样例均可读取为标准 `timeseries.csv`。
- 已通过：两个样例均包含 `physical_time`、24 个 `JET_XX`、24 个 `cmd_massflow_XX`、24 个 `actual_massflow_XX`、六个 Fz 传感器、`Fz_Total`、`Drag_Total`、`Pitch_Moment`、`Roll_Moment`、`Jet_Reaction_Z`。
- 已通过：`physical_time` 单调递增，采样步长约 `1e-4 s`，采样频率约 `10000 Hz`。
- 已通过：喷气样例 `temp_j01_3s_mass2p57_dt1e-4` 的质量检查为 `0 error / 0 warning`。
- 待约定：STAR 原始导出列名目前可映射，但后续正式算例需固定命名规则，避免中文 monitor 名或文件名变化导致映射失效。

## 单位、方向和采样频率问题

- 已记录单位：力 `N`，力矩 `N-m`，质量流量 `kg/s`。
- 待浩坤确认：`positive values follow the STAR-CCM+ monitor export convention` 仍然偏泛，需要明确 Fz、Drag、Pitch、Roll、Jet_Reaction_Z 的正方向。
- 已确认采样频率：两个样例都是 `dt = 0.0001 s`，约 `10000 Hz`，总时长 `3.0 s`，共 `30000` 行。
- 待约定：后续 CFD 算例是否保持 `dt = 1e-4 s` 输出，还是允许降采样；如果允许降采样，需要在 manifest 中写清楚。

## 物理问题

- 无喷气样例 `temp_no_jet_3s_dt1e-4` 的质量检查为 `0 error / 1 warning`。
- warning 内容：无喷气 case 中 `Jet_Reaction_Z` 非零，最大绝对值约 `2.97e3 N`。
- 这不是 CSV 格式问题，也不是博超侧读取失败；它是物理量定义或 STAR monitor 口径问题。
- 需要浩坤确认：无喷气情况下 `Jet_Reaction_Z` 是否应严格为零；如果不应为零，它代表哪个力、哪个边界或哪个参考方向。

## 下一步对接

- 博超展示：`actuation_schedule.csv` 生成、`timeseries.csv` 读取、质量检查和自动图。
- 浩坤展示：STAR 中如何使用 `actuation_schedule.csv`，以及各 monitor 的列名、单位和正方向。
- 联合确认：下一个 CFD case 先跑 `B07_J01_repeat_3s_mass2p57` 或 `B07_no_jet_reference_repeat`，优先排查可重复性和无喷气 `Jet_Reaction_Z` 口径。
